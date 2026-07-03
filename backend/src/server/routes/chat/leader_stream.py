"""Leader Agent 事件流处理。"""

import asyncio
import json
from dataclasses import dataclass, field
from typing import AsyncGenerator

from agentscope._utils._common import _generate_id
from agentscope.message import Msg, UserMsg
from agentscope.tool import ToolChoice

from src.server.routes.chat.helpers import _set_skill_run_status, _sse_event
from src.server.routes.chat.hitl import _create_pending_interaction, _hitl_payload, _merge_hitl_tool_calls
from src.server.routes.chat.models import (
    HITL_EVENT_TYPES,
    ChatModelCacheRun,
    ChatSkillRun,
    ChatTeamActivity,
    ChatToolRun,
)
from src.server.routes.chat.prompts import _extract_clarification_request
from src.server.routes.chat.runtime_events import (
    runtime_event_from_agentscope_sse,
    runtime_event_sse,
)
from src.server.routes.chat.skill_runs import update_skill_run_status
from src.server.routes.chat.store import _append_assistant_message
from src.server.routes.chat.team_state import (
    _mark_team_leader_waiting_for_clarification,
    _update_worker_tool_calls,
)
from src.server.routes.chat.trace_recorder import (
    _attach_provider_cache_usage,
    build_tool_artifact_runtime_event,
    _model_cache_from_event,
    _record_agent_scope_event,
    _record_permission_decision,
    _record_run_event,
    _record_tool_execution_artifacts,
)


@dataclass
class LeaderStreamState:
    """Leader Agent 流式执行中累计的状态。"""

    assistant_text: str = ""
    assistant_reasoning_text: str = ""
    assistant_thinking: str = ""
    assistant_tool_calls: list[ChatToolRun] = field(default_factory=list)
    assistant_model_caches: list[ChatModelCacheRun] = field(default_factory=list)
    runtime_events: list[dict] = field(default_factory=list)
    final_answer_started: bool = False
    final_answer_tool_call_ids: set[str] = field(default_factory=set)
    active_skill_run: dict | None = None
    stopped: bool = False
    stop_reason: str = ""
    exceeded_max_iters: bool = False
    pending_plan_finalized: bool = False
    pending_plan_final_answer: str = ""
    final_answer_recovery_attempted: bool = False


_PLAN_TOOL_RUNTIME_EVENTS = {
    "plan_enter": ("PLAN_MODE_ENTERED", "running"),
    "plan_write": ("PLAN_UPDATED", "running"),
    "plan_exit": ("PLAN_FINALIZED", "completed"),
    "todo_write": ("PLAN_TASKS_UPDATED", "running"),
    "task_output": ("PLAN_TASK_OUTPUT", ""),
}
_PLAN_FINAL_ANSWER_CHUNK_SIZE = 24
_PLAN_FINAL_ANSWER_CHUNK_DELAY_SECONDS = 0.012


async def stream_leader_agent_reply(
    *,
    agent: object,
    user_msg: object,
    state: LeaderStreamState,
    session_id: str,
    persist: bool,
    agent_id: str,
    agent_name: str,
    model_name: str,
    guardrails: object,
    message: str,
    stream_scope: str,
    skill_runs: list[ChatSkillRun],
    team_activities: list[ChatTeamActivity],
    plan_mode_manager: object | None = None,
) -> AsyncGenerator[str, None]:
    """消费 Leader AgentScope 事件流，并处理 HITL / clarification 早停。"""

    async for event in agent.reply_stream(user_msg):
        event_data = event.model_dump(mode="json")
        event_type = event_data.pop("type", "UNKNOWN")
        if event_type == "EXCEED_MAX_ITERS":
            state.exceeded_max_iters = True
            yield runtime_event_sse(
                "AGENT_FAILED",
                session_id=session_id,
                scope=stream_scope,
                source="agentscope",
                source_event_type=event_type,
                agent_id=agent_id,
                agent_name=agent_name,
                status="failed",
                reason="max_react_iterations",
                payload={
                    "replyId": str(event_data.get("reply_id") or ""),
                    "name": str(event_data.get("name") or agent_name),
                },
                collect=state.runtime_events,
            )
            break
        if plan_mode_manager is not None:
            for runtime_event in plan_mode_manager.drain_runtime_events():
                state.runtime_events.append(runtime_event)
                yield _sse_event("RUNTIME_EVENT", runtime_event)
                if runtime_event.get("type") == "PLAN_FINALIZED":
                    _remember_pending_plan_finalized(state, runtime_event)
        if _is_final_answer_tool_event(event_type, event_data, state):
            if event_type == "TOOL_CALL_START":
                state.final_answer_started = True
                yield runtime_event_sse(
                    "AGENT_FINAL_ANSWER_STARTED",
                    session_id=session_id,
                    scope=stream_scope,
                    source="runtime",
                    source_event_type=event_type,
                    agent_id=agent_id,
                    agent_name=agent_name,
                    status="running",
                    payload={
                        "tool_call_id": str(event_data.get("tool_call_id") or ""),
                        "tool_call_name": str(event_data.get("tool_call_name") or "FinalAnswerStart"),
                    },
                    collect=state.runtime_events,
                )
            continue
        _attach_provider_cache_usage(event_type, event_data)
        if event_type == "MODEL_CALL_END":
            cache_entry = _model_cache_from_event(event_data, model_name)
            if cache_entry:
                state.assistant_model_caches.append(cache_entry)

        _record_agent_scope_event(
            session_id=session_id,
            event_type=event_type,
            event_data=event_data,
            persist=persist,
            scope=stream_scope,
            agent_id=agent_id,
            agent_name=agent_name,
        )
        _record_permission_decision(
            session_id=session_id,
            event_type=event_type,
            event_data=event_data,
            persist=persist,
            scope=stream_scope,
            agent_id=agent_id,
            agent_name=agent_name,
            guardrails=guardrails,
        )
        next_tool_calls = _update_worker_tool_calls(
            state.assistant_tool_calls,
            event_type,
            event_data,
        )
        if next_tool_calls is not state.assistant_tool_calls:
            state.assistant_tool_calls = next_tool_calls
        plan_event = _plan_runtime_event_from_tool_result(
            tool_calls=state.assistant_tool_calls,
            runtime_events=state.runtime_events,
            event_type=event_type,
            event_data=event_data,
            session_id=session_id,
            agent_id=agent_id,
            agent_name=agent_name,
        )
        if plan_event:
            yield plan_event
            plan_answer = _plan_final_answer_from_sse(plan_event)
            if plan_answer:
                _remember_pending_plan_finalized_from_text(state, plan_answer)
        _record_tool_execution_artifacts(
            session_id=session_id,
            event_type=event_type,
            event_data=event_data,
            persist=persist,
            scope=stream_scope,
            agent_id=agent_id,
            agent_name=agent_name,
            team_activity_id=None,
            guardrails=guardrails,
            tool_calls=state.assistant_tool_calls,
        )
        artifact_event = build_tool_artifact_runtime_event(
            event_type=event_type,
            event_data=event_data,
            tool_calls=state.assistant_tool_calls,
        )
        if artifact_event:
            artifact_type, artifact_payload = artifact_event
            yield runtime_event_sse(
                artifact_type,
                session_id=session_id,
                scope="artifact",
                source="runtime",
                source_event_type=event_type,
                agent_id=agent_id,
                agent_name=agent_name,
                status=str(artifact_payload.get("status") or "completed"),
                payload=artifact_payload,
                collect=state.runtime_events,
            )

        if event_type in HITL_EVENT_TYPES:
            if state.active_skill_run and persist:
                state.active_skill_run = update_skill_run_status(
                    state.active_skill_run,
                    status="waiting_user",
                    current_step="waiting_hitl_resume",
                )
            agentscope_waiting_tool_calls = list(event_data.get("tool_calls") or [])
            interaction = _create_pending_interaction(
                interaction_type=event_type,
                agent=agent,
                session_id=session_id,
                agent_id=agent_id,
                agent_name=agent_name,
                model_name=model_name,
                reply_id=str(event_data.get("reply_id") or ""),
                tool_calls=_merge_hitl_tool_calls(
                    agentscope_waiting_tool_calls,
                    state.assistant_tool_calls,
                ),
                resume_tool_calls=agentscope_waiting_tool_calls,
                message=message,
                assistant_text=state.assistant_text,
                assistant_tool_calls=state.assistant_tool_calls,
                skill_runs=skill_runs,
                team_activities=team_activities,
                scope=stream_scope,
                persist=persist,
            )
            yield runtime_event_sse(
                "AGENT_WAITING_HUMAN",
                session_id=session_id,
                scope=stream_scope,
                source="runtime",
                source_event_type=event_type,
                agent_id=agent_id,
                agent_name=agent_name,
                status="waiting",
                reason="hitl_required",
                payload={"interactionId": interaction.id, "interactionType": event_type},
                collect=state.runtime_events,
            )
            yield _sse_event("HITL_REQUIRED", _hitl_payload(interaction))
            state.stopped = True
            state.stop_reason = "hitl"
            return

        if event_type == "TEXT_BLOCK_DELTA":
            delta = str(event_data.get("delta", ""))
            if state.final_answer_started:
                state.assistant_text += delta
                yield runtime_event_sse(
                    "AGENT_FINAL_ANSWER_DELTA",
                    session_id=session_id,
                    scope=stream_scope,
                    source="runtime",
                    source_event_type=event_type,
                    agent_id=agent_id,
                    agent_name=agent_name,
                    status="running",
                    payload={"delta": delta},
                    collect=state.runtime_events,
                )
                yield _sse_event("FINAL_ANSWER_DELTA", {"delta": delta})
            else:
                state.assistant_reasoning_text += delta
                yield runtime_event_sse(
                    "AGENT_REASONING_DELTA",
                    session_id=session_id,
                    scope=stream_scope,
                    source="runtime",
                    source_event_type=event_type,
                    agent_id=agent_id,
                    agent_name=agent_name,
                    status="running",
                    payload={"delta": delta},
                    collect=state.runtime_events,
                )
                yield _sse_event("REASONING_DELTA", {"delta": delta})
            continue
        elif event_type == "THINKING_BLOCK_DELTA":
            state.assistant_thinking += str(event_data.get("delta", ""))

        if event_type == "REPLY_END" and skill_runs:
            completed_skill_runs = _set_skill_run_status(skill_runs, "completed")
            for skill in completed_skill_runs:
                yield runtime_event_sse(
                    "SKILL_COMPLETED",
                    session_id=session_id,
                    scope="skill",
                    agent_id=agent_id,
                    agent_name=agent_name,
                    status="completed",
                    payload=skill.model_dump(by_alias=True),
                    collect=state.runtime_events,
                )
            yield _sse_event("SKILL_END", {
                "skills": [item.model_dump(by_alias=True) for item in completed_skill_runs],
                "status": "completed",
            })

        runtime_event = runtime_event_from_agentscope_sse(
            event_type,
            session_id=session_id,
            scope=stream_scope,
            agent_id=agent_id,
            agent_name=agent_name,
            event_data=event_data,
            collect=state.runtime_events,
        )
        if runtime_event:
            yield runtime_event
        yield _sse_event(event_type, event_data)
        if _is_pending_plan_finalization_ready(state, event_type, event_data):
            async for final_event in _stream_pending_plan_final_answer_events(
                state,
                session_id=session_id,
                stream_scope=stream_scope,
                agent_id=agent_id,
                agent_name=agent_name,
            ):
                yield final_event
            return

    clarification = (
        _extract_clarification_request(state.assistant_text)
        or _extract_clarification_request(state.assistant_reasoning_text)
        or _extract_clarification_request(state.assistant_thinking)
    )
    if clarification:
        if state.active_skill_run:
            state.active_skill_run = update_skill_run_status(
                state.active_skill_run,
                status="waiting_user",
                current_step=str(clarification.get("title") or "waiting_user"),
                pending_interaction_id=str(clarification.get("id") or ""),
            )
        _mark_team_leader_waiting_for_clarification(team_activities, agent_id)
        if team_activities:
            yield _sse_event("TEAM_ACTIVITY", {
                "activities": [item.model_dump(by_alias=True) for item in team_activities],
            })
        _record_run_event(
            session_id=session_id,
            event_type="CLARIFICATION_REQUIRED",
            persist=persist,
            scope=stream_scope,
            agent_id=agent_id,
            agent_name=agent_name,
            status="waiting",
            title=str(clarification.get("title") or "Clarification required"),
            payload=clarification,
        )
        if persist:
            _append_assistant_message(
                session_id=session_id,
                agent_id=agent_id,
                agent_name=agent_name,
                model_name=model_name,
                content="",
                error=None,
                thinking=state.assistant_thinking,
                skill_calls=skill_runs,
                tool_calls=state.assistant_tool_calls,
                team_activities=team_activities,
                model_caches=state.assistant_model_caches,
                runtime_events=state.runtime_events,
                clarification_requests=[clarification],
            )
        yield runtime_event_sse(
            "CLARIFICATION_WAITING",
            session_id=session_id,
            scope=stream_scope,
            agent_id=agent_id,
            agent_name=agent_name,
            status="waiting",
            reason="clarification_required",
            payload=clarification,
            collect=state.runtime_events,
        )
        yield _sse_event("CLARIFICATION_REQUIRED", clarification)
        state.stopped = True
        state.stop_reason = "clarification"
        return

    if _should_attempt_final_answer_recovery(state):
        async for recovery_event in _stream_final_answer_recovery_events(
            agent=agent,
            state=state,
            session_id=session_id,
            persist=persist,
            agent_id=agent_id,
            agent_name=agent_name,
            model_name=model_name,
            guardrails=guardrails,
            message=message,
            stream_scope=stream_scope,
        ):
            yield recovery_event


def _should_attempt_final_answer_recovery(state: LeaderStreamState) -> bool:
    """判断是否进入受限最终答案恢复流程。"""

    return (
        not state.final_answer_started
        and not state.final_answer_recovery_attempted
        and not state.stopped
        and not state.assistant_text.strip()
        and (
            state.exceeded_max_iters
            or bool(state.assistant_reasoning_text.strip())
            or bool(state.assistant_thinking.strip())
            or bool(state.assistant_tool_calls)
        )
    )


async def _stream_final_answer_recovery_events(
    *,
    agent: object,
    state: LeaderStreamState,
    session_id: str,
    persist: bool,
    agent_id: str,
    agent_name: str,
    model_name: str,
    guardrails: object,
    message: str,
    stream_scope: str,
) -> AsyncGenerator[str, None]:
    """在模型漏调 FinalAnswerStart 时执行一次受限最终答案恢复。"""

    state.final_answer_recovery_attempted = True
    if not all(
        hasattr(agent, attr)
        for attr in ("_handle_incoming_messages", "_reasoning", "_batch_tool_calls")
    ):
        return

    recovery_prompt = _build_final_answer_recovery_prompt(
        user_message=message,
        previous_text=state.assistant_reasoning_text,
        thinking_text=state.assistant_thinking,
        tool_calls=state.assistant_tool_calls,
        exceeded_max_iters=state.exceeded_max_iters,
    )
    try:
        await agent._handle_incoming_messages(UserMsg(name="user", content=recovery_prompt))
        agent.state.reply_id = _generate_id()
        agent.state.cur_iter = 0
        marker_choice = ToolChoice(mode="FinalAnswerStart", tools=["FinalAnswerStart"])
        async for event in agent._reasoning(tool_choice=marker_choice):
            async for sse_event in _handle_final_answer_recovery_event(
                event,
                state=state,
                session_id=session_id,
                persist=persist,
                agent_id=agent_id,
                agent_name=agent_name,
                model_name=model_name,
                guardrails=guardrails,
                stream_scope=stream_scope,
            ):
                yield sse_event

        async for event in _execute_recovery_tool_calls(agent):
            async for sse_event in _handle_final_answer_recovery_event(
                event,
                state=state,
                session_id=session_id,
                persist=persist,
                agent_id=agent_id,
                agent_name=agent_name,
                model_name=model_name,
                guardrails=guardrails,
                stream_scope=stream_scope,
            ):
                yield sse_event

        if not state.final_answer_started:
            return

        answer_choice = ToolChoice(mode="none")
        async for event in agent._reasoning(tool_choice=answer_choice):
            async for sse_event in _handle_final_answer_recovery_event(
                event,
                state=state,
                session_id=session_id,
                persist=persist,
                agent_id=agent_id,
                agent_name=agent_name,
                model_name=model_name,
                guardrails=guardrails,
                stream_scope=stream_scope,
            ):
                yield sse_event
        if state.assistant_text.strip():
            state.exceeded_max_iters = False
    except Exception as exc:
        yield runtime_event_sse(
            "AGENT_FAILED",
            session_id=session_id,
            scope=stream_scope,
            source="runtime",
            source_event_type="FINAL_ANSWER_RECOVERY",
            agent_id=agent_id,
            agent_name=agent_name,
            status="failed",
            reason="final_answer_recovery_failed",
            payload={"message": str(exc)},
            collect=state.runtime_events,
        )


async def _execute_recovery_tool_calls(agent: object) -> AsyncGenerator[object, None]:
    """执行恢复轮强制生成的 FinalAnswerStart 工具调用。"""

    for batch in await agent._batch_tool_calls():
        if batch.type == "sequential":
            event_generator = agent._execute_sequential_tool_calls(batch.tool_calls)
        else:
            event_generator = agent._execute_concurrent_tool_calls(batch.tool_calls)
        async for event in event_generator:
            yield event
        break


async def _handle_final_answer_recovery_event(
    event: object,
    *,
    state: LeaderStreamState,
    session_id: str,
    persist: bool,
    agent_id: str,
    agent_name: str,
    model_name: str,
    guardrails: object,
    stream_scope: str,
) -> AsyncGenerator[str, None]:
    """把恢复轮 AgentScope 事件映射回现有 Chat SSE。"""

    if isinstance(event, Msg):
        return
    event_data = event.model_dump(mode="json")
    event_type = event_data.pop("type", "UNKNOWN")
    if _is_final_answer_tool_event(event_type, event_data, state):
        if event_type == "TOOL_CALL_START":
            state.final_answer_started = True
            yield runtime_event_sse(
                "AGENT_FINAL_ANSWER_STARTED",
                session_id=session_id,
                scope=stream_scope,
                source="runtime",
                source_event_type=event_type,
                agent_id=agent_id,
                agent_name=agent_name,
                status="running",
                reason="recover_missing_final_answer_marker",
                payload={
                    "tool_call_id": str(event_data.get("tool_call_id") or ""),
                    "tool_call_name": str(event_data.get("tool_call_name") or "FinalAnswerStart"),
                    "recovered": True,
                },
                collect=state.runtime_events,
            )
        return

    _attach_provider_cache_usage(event_type, event_data)
    if event_type == "MODEL_CALL_END":
        cache_entry = _model_cache_from_event(event_data, model_name)
        if cache_entry:
            state.assistant_model_caches.append(cache_entry)

    _record_agent_scope_event(
        session_id=session_id,
        event_type=event_type,
        event_data=event_data,
        persist=persist,
        scope=stream_scope,
        agent_id=agent_id,
        agent_name=agent_name,
    )
    _record_permission_decision(
        session_id=session_id,
        event_type=event_type,
        event_data=event_data,
        persist=persist,
        scope=stream_scope,
        agent_id=agent_id,
        agent_name=agent_name,
        guardrails=guardrails,
    )

    if event_type == "TEXT_BLOCK_DELTA":
        delta = str(event_data.get("delta", ""))
        if state.final_answer_started:
            state.assistant_text += delta
            yield runtime_event_sse(
                "AGENT_FINAL_ANSWER_DELTA",
                session_id=session_id,
                scope=stream_scope,
                source="runtime",
                source_event_type="FINAL_ANSWER_RECOVERY",
                agent_id=agent_id,
                agent_name=agent_name,
                status="running",
                reason="recover_missing_final_answer_marker",
                payload={"delta": delta, "recovered": True},
                collect=state.runtime_events,
            )
            yield _sse_event("FINAL_ANSWER_DELTA", {"delta": delta})
        return
    if event_type == "THINKING_BLOCK_DELTA":
        state.assistant_thinking += str(event_data.get("delta", ""))
        return

    runtime_event = runtime_event_from_agentscope_sse(
        event_type,
        session_id=session_id,
        scope=stream_scope,
        agent_id=agent_id,
        agent_name=agent_name,
        event_data=event_data,
        collect=state.runtime_events,
    )
    if runtime_event:
        yield runtime_event
    yield _sse_event(event_type, event_data)


def _build_final_answer_recovery_prompt(
    *,
    user_message: str,
    previous_text: str,
    thinking_text: str = "",
    tool_calls: list[ChatToolRun] | None = None,
    exceeded_max_iters: bool = False,
) -> str:
    """构造受限最终答案恢复提示。"""

    compact_previous = previous_text.strip()
    if len(compact_previous) > 6000:
        compact_previous = compact_previous[-6000:]
    compact_thinking = thinking_text.strip()
    if len(compact_thinking) > 3000:
        compact_thinking = compact_thinking[-3000:]
    tool_summary = _build_recovery_tool_summary(tool_calls or [])
    return "\n\n".join([
        "Final answer recovery is required.",
        (
            "The previous ReAct loop reached the maximum iteration limit before producing a final answer."
            if exceeded_max_iters
            else "The previous response ended without calling FinalAnswerStart, so it was treated as process text."
        ),
        "Do not perform new investigation or call any tool except FinalAnswerStart.",
        "First call FinalAnswerStart exactly once. After that tool succeeds, provide the final user-facing answer as normal assistant text.",
        "Use the available process text, thinking summary, and tool results below to produce the best possible final answer.",
        "If the available information is incomplete, still answer concisely with what is known and mention what could not be verified; do not ask a new question here.",
        "Original user request:",
        user_message,
        "Previous process text to synthesize from:",
        compact_previous or "(none)",
        "Previous thinking summary:",
        compact_thinking or "(none)",
        "Tool call summary:",
        tool_summary or "(none)",
    ])


def _build_recovery_tool_summary(tool_calls: list[ChatToolRun]) -> str:
    """把已发生工具调用压缩为最终答案恢复上下文。"""

    lines: list[str] = []
    for index, tool_call in enumerate(tool_calls[-8:], start=1):
        name = str(getattr(tool_call, "name", "") or "tool")
        status = str(getattr(tool_call, "status", "") or "")
        input_text = _trim_recovery_text(str(getattr(tool_call, "args", "") or ""))
        result_text = _trim_recovery_text(str(getattr(tool_call, "result", "") or ""))
        lines.append(
            f"{index}. {name} [{status}]\n"
            f"Input: {input_text or '(empty)'}\n"
            f"Result: {result_text or '(empty)'}"
        )
    return "\n\n".join(lines)


def _trim_recovery_text(text: str, limit: int = 900) -> str:
    """限制恢复上下文中的单段文本长度。"""

    value = text.strip()
    if len(value) <= limit:
        return value
    return value[: limit // 2] + "\n...[trimmed]...\n" + value[-limit // 2:]


def _is_final_answer_tool_event(
    event_type: str,
    event_data: dict,
    state: LeaderStreamState,
) -> bool:
    """Return whether an AgentScope tool event belongs to the phase marker tool."""

    tool_call_id = str(event_data.get("tool_call_id") or "")
    tool_name = str(event_data.get("tool_call_name") or "")
    normalized = tool_name.replace(" ", "").replace("_", "").lower()
    if event_type == "TOOL_CALL_START" and normalized.endswith("finalanswerstart"):
        if tool_call_id:
            state.final_answer_tool_call_ids.add(tool_call_id)
        return True
    return bool(tool_call_id and tool_call_id in state.final_answer_tool_call_ids and event_type.startswith("TOOL_"))


def _plan_runtime_event_from_tool_result(
    *,
    tool_calls: list[ChatToolRun],
    runtime_events: list[dict],
    event_type: str,
    event_data: dict,
    session_id: str,
    agent_id: str,
    agent_name: str,
) -> str | None:
    """从 Plan 控制工具结果兜底生成实时 Runtime Event。"""

    if event_type != "TOOL_RESULT_END":
        return None
    tool_call_id = str(event_data.get("tool_call_id") or "")
    tool_call = next((item for item in tool_calls if item.id == tool_call_id), None)
    if not tool_call:
        return None
    tool_name = tool_call.name
    runtime_info = _PLAN_TOOL_RUNTIME_EVENTS.get(tool_name)
    if not runtime_info:
        return None
    try:
        result = json.loads(tool_call.result or "{}")
    except json.JSONDecodeError:
        return None
    if not isinstance(result, dict):
        return None

    plan_id = str(result.get("planId") or "")
    if not plan_id:
        return None
    runtime_type, default_status = runtime_info
    if _has_existing_plan_tool_runtime_event(
        runtime_events,
        runtime_type=runtime_type,
        plan_id=plan_id,
        tool_call_id=tool_call_id,
        result=result,
    ):
        return None

    payload = {
        "planId": plan_id,
        "planFile": str(result.get("planFile") or ""),
        "toolCallId": tool_call_id,
    }
    if tool_name == "plan_enter":
        payload["mode"] = "plan"
    if "markdown" in result:
        payload["markdown"] = str(result.get("markdown") or "")
    if "steps" in result:
        payload["steps"] = result.get("steps") or []
    if "step" in result:
        payload["step"] = result.get("step") or {}
    status = default_status or _runtime_status_from_plan_tool_result(result)
    return runtime_event_sse(
        runtime_type,
        session_id=session_id,
        scope="plan",
        source="runtime",
        source_event_type=event_type,
        agent_id=agent_id,
        agent_name=agent_name,
        status=status,
        reason="Plan finalized as final answer." if runtime_type == "PLAN_FINALIZED" else "",
        payload=payload,
        collect=runtime_events,
    )


def _plan_final_answer_from_sse(event: str) -> str:
    """从 PLAN_FINALIZED SSE 中提取最终答复文本。"""

    event_type, data = _parse_plan_sse_event(event)
    if event_type != "RUNTIME_EVENT" or data.get("type") != "PLAN_FINALIZED":
        return ""
    return _plan_final_answer_from_runtime_event(data)


def _plan_final_answer_from_runtime_event(runtime_event: dict) -> str:
    """从 PLAN_FINALIZED RuntimeEvent 中提取计划 markdown。"""

    payload = runtime_event.get("payload") if isinstance(runtime_event.get("payload"), dict) else {}
    markdown = str(payload.get("markdown") or "").strip()
    return markdown


def _remember_pending_plan_finalized(state: LeaderStreamState, runtime_event: dict) -> None:
    """暂存 PLAN_FINALIZED，等 plan_exit 工具结果闭合后再输出最终答案。"""

    state.pending_plan_finalized = True
    plan_answer = _plan_final_answer_from_runtime_event(runtime_event)
    if plan_answer:
        state.pending_plan_final_answer = plan_answer


def _remember_pending_plan_finalized_from_text(state: LeaderStreamState, plan_answer: str) -> None:
    """暂存工具结果兜底生成的 finalized plan 文本。"""

    state.pending_plan_finalized = True
    if plan_answer:
        state.pending_plan_final_answer = plan_answer


def _is_pending_plan_finalization_ready(
    state: LeaderStreamState,
    event_type: str,
    event_data: dict,
) -> bool:
    """判断 plan_exit 工具结果是否已经闭合，可以安全停止 Agent 流。"""

    if not state.pending_plan_finalized or event_type != "TOOL_RESULT_END":
        return False
    tool_call_id = str(event_data.get("tool_call_id") or "")
    tool_name = str(event_data.get("tool_call_name") or "")
    if not tool_name and tool_call_id:
        tool_call = next((item for item in state.assistant_tool_calls if item.id == tool_call_id), None)
        tool_name = tool_call.name if tool_call else ""
    return _is_plan_exit_tool_name(tool_name)


def _is_plan_exit_tool_name(tool_name: str) -> bool:
    normalized = tool_name.replace(" ", "").replace("_", "").lower()
    return normalized == "planexit"


async def _stream_pending_plan_final_answer_events(
    state: LeaderStreamState,
    *,
    session_id: str,
    stream_scope: str,
    agent_id: str,
    agent_name: str,
) -> AsyncGenerator[str, None]:
    """流式输出暂存的 Plan 最终答案并标记本轮计划收口。"""

    plan_answer = state.pending_plan_final_answer
    state.pending_plan_finalized = False
    state.pending_plan_final_answer = ""
    state.stopped = True
    state.stop_reason = "plan_finalized"
    if not plan_answer:
        return
    state.final_answer_started = True
    state.assistant_text += plan_answer
    deltas = _iter_plan_final_answer_deltas(plan_answer)
    for index, delta in enumerate(deltas):
        yield _sse_event("FINAL_ANSWER_DELTA", {"delta": delta})
        yield runtime_event_sse(
            "AGENT_FINAL_ANSWER_DELTA",
            session_id=session_id,
            scope=stream_scope,
            source="runtime",
            source_event_type="PLAN_FINALIZED",
            agent_id=agent_id,
            agent_name=agent_name,
            status="completed",
            payload={"delta": delta},
            collect=state.runtime_events,
        )
        if index < len(deltas) - 1:
            await asyncio.sleep(_PLAN_FINAL_ANSWER_CHUNK_DELAY_SECONDS)


def _iter_plan_final_answer_deltas(
    text: str,
    chunk_size: int = _PLAN_FINAL_ANSWER_CHUNK_SIZE,
) -> list[str]:
    """把 Plan 最终答案拆成适合前端逐步渲染的文本片段。"""

    if not text:
        return []
    chunks: list[str] = []
    buffer = ""
    for char in text:
        buffer += char
        if char == "\n" or len(buffer) >= chunk_size:
            chunks.append(buffer)
            buffer = ""
    if buffer:
        chunks.append(buffer)
    return chunks


def _parse_plan_sse_event(event: str) -> tuple[str, dict]:
    event_type = ""
    data_text = ""
    for line in event.splitlines():
        if line.startswith("event: "):
            event_type = line.removeprefix("event: ").strip()
        elif line.startswith("data: "):
            data_text = line.removeprefix("data: ").strip()
    if not data_text:
        return event_type, {}
    try:
        data = json.loads(data_text)
    except json.JSONDecodeError:
        return event_type, {}
    return event_type, data if isinstance(data, dict) else {}


def _has_existing_plan_tool_runtime_event(
    runtime_events: list[dict],
    *,
    runtime_type: str,
    plan_id: str,
    tool_call_id: str,
    result: dict,
) -> bool:
    """判断工具结果是否已经由 manager 原生事件实时发送过。"""

    result_step_id = str((result.get("step") or {}).get("id") or "")
    result_steps = result.get("steps") if isinstance(result.get("steps"), list) else None
    for event in runtime_events:
        payload = event.get("payload", {})
        if event.get("type") != runtime_type or str(payload.get("planId") or "") != plan_id:
            continue
        if tool_call_id and str(payload.get("toolCallId") or "") == tool_call_id:
            return True
        if payload.get("toolCallId"):
            continue
        if runtime_type == "PLAN_TASK_OUTPUT":
            payload_step_id = str((payload.get("step") or {}).get("id") or "")
            if result_step_id and payload_step_id == result_step_id:
                return True
            continue
        if runtime_type == "PLAN_TASKS_UPDATED":
            if result_steps is None:
                return True
            payload_steps = payload.get("steps")
            if isinstance(payload_steps, list):
                # 比较 JSON 序列化字符串，避免因对象引用不同导致误判
                if json.dumps(payload_steps, sort_keys=True, default=str) == json.dumps(
                    result_steps, sort_keys=True, default=str
                ):
                    return True
            continue
        return True
    return False


def _runtime_status_from_plan_tool_result(result: dict) -> str:
    status = str((result.get("step") or {}).get("status") or "").lower()
    if status == "failed":
        return "failed"
    if status in {"completed", "skipped"}:
        return "completed"
    return "running"
