"""Leader Agent 事件流处理。"""

import asyncio
import json
from dataclasses import dataclass, field
from typing import AsyncGenerator

from agentscope.event import ExternalExecutionResultEvent
from agentscope.message import UserMsg

from src.server.routes.chat.helpers import _set_skill_run_status, source_event
from src.server.routes.chat.hitl import _create_pending_interaction, _hitl_payload
from src.server.routes.chat.lifecycle import waiting_reason, waiting_status_for_interaction
from src.server.routes.chat.models import (
    HITL_EVENT_TYPES,
    PENDING_INTERACTIONS,
    ChatModelCacheRun,
    ChatSkillRun,
    ChatToolRun,
)
from src.server.routes.chat.prompts import _extract_clarification_request
from src.server.routes.chat.final_answer_recovery import (
    _is_final_answer_tool_event,
    stream_final_answer_recovery_events as _stream_final_answer_recovery_events,
)
from src.server.routes.chat.runtime_events import (
    runtime_event_from_agentscope_event,
    runtime_trace_source_event,
    runtime_source_event,
)
from src.server.routes.chat.projection.source_events import SourceEvent
from src.server.routes.chat.skill_runs import start_or_resume_skill_run, update_skill_run_status
from src.server.routes.skills import Skill, list_skill_usage_details, resolve_installed_skills
from src.server.routes.chat.store import _append_assistant_message
from src.server.routes.chat.tool_state import _update_tool_calls
from src.server.routes.chat.trace_recorder import (
    _attach_provider_cache_usage,
    build_tool_artifact_runtime_event,
    _model_cache_from_event,
    _record_agent_scope_event,
    _record_permission_decision,
    _record_run_event,
    _record_tool_execution_artifacts,
)
from src.subagents.manager import SubagentDelegationManager


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
    pending_plan_review: bool = False
    pending_plan_review_answer: str = ""
    final_answer_recovery_attempted: bool = False
    pending_text: str = ""
    reply_had_tool_call: bool = False
    available_skill_details: list[dict] = field(default_factory=list)
    session_workspace_dir: str = ""
    session_artifact_paths_before: list[str] = field(default_factory=list)
    subagent_completion_blocked_reason: str = ""
    subagent_completion_block_count: int = 0
    blocked_final_text: str = ""


_PLAN_FINAL_ANSWER_CHUNK_DELAY_SECONDS = 0.012


def _collect_pending_tool_batch(
    batches: list[dict],
    interaction_type: str,
    event_data: dict,
) -> None:
    """合并同一回复、同一等待类型的 AgentScope 工具调用。"""

    reply_id = str(event_data.get("reply_id") or "")
    batch = next(
        (
            item
            for item in batches
            if item["type"] == interaction_type and item["reply_id"] == reply_id
        ),
        None,
    )
    if batch is None:
        batch = {
            "type": interaction_type,
            "reply_id": reply_id,
            "tool_calls": [],
        }
        batches.append(batch)

    tool_calls = batch["tool_calls"]
    seen_ids = {str(tool_call.get("id") or "") for tool_call in tool_calls}
    for tool_call in event_data.get("tool_calls") or []:
        if not isinstance(tool_call, dict):
            continue
        tool_call_id = str(tool_call.get("id") or "")
        if not tool_call_id or tool_call_id in seen_ids:
            continue
        tool_calls.append(dict(tool_call))
        seen_ids.add(tool_call_id)


def _flush_pending_reasoning_events(
    state: LeaderStreamState,
    *,
    session_id: str,
    stream_scope: str,
    source_event_type: str,
    agent_id: str,
    agent_name: str,
) -> list[SourceEvent]:
    """把工具调用前累计的普通文本立即投成独立 reasoning 段。"""

    pending = state.pending_text
    if not pending:
        return []
    state.pending_text = ""
    state.assistant_reasoning_text += pending
    return [
        runtime_trace_source_event(
            "AGENT_REASONING_DELTA",
            session_id=session_id,
            scope=stream_scope,
            source="runtime",
            source_event_type=source_event_type,
            agent_id=agent_id,
            agent_name=agent_name,
            status="running",
            payload={"delta": pending},
            collect=state.runtime_events,
        ),
        source_event("REASONING_DELTA", {"delta": pending}),
    ]


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
    plan_mode_manager: object | None = None,
    runtime_event_sources: list[object] | None = None,
    sandbox: bool = False,
) -> AsyncGenerator[str, None]:
    """消费 Leader AgentScope 事件流，并处理 HITL / clarification 早停。"""

    # AgentScope 会为同一并行批次中的每个待处理工具分别发出一个
    # REQUIRE_* 事件。必须先排空该批次，确保 Agent 内部已把所有工具标为
    # waiting，再把准确集合交给前端；收到第一个事件就返回会导致 resume
    # 回传一个 Agent 实际未等待的工具 ID。
    pending_hitl_batches: list[dict] = []
    pending_subagent_batches: list[dict] = []

    async for event in agent.reply_stream(user_msg):
        event_data = event.model_dump(mode="json")
        event_type = event_data.pop("type", "UNKNOWN")
        if event_type == "REPLY_START":
            state.pending_text = ""
            state.reply_had_tool_call = False
        elif event_type in {"TOOL_CALL_START", "TOOL_CALL_DELTA", "TOOL_CALL_END"}:
            state.reply_had_tool_call = True
        if event_type == "EXCEED_MAX_ITERS":
            state.exceeded_max_iters = True
            for reasoning_event in _flush_pending_reasoning_events(
                state,
                session_id=session_id,
                stream_scope=stream_scope,
                source_event_type=event_type,
                agent_id=agent_id,
                agent_name=agent_name,
            ):
                yield reasoning_event
            yield runtime_trace_source_event(
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
                yield runtime_source_event(runtime_event)
                if runtime_event.get("type") == "PLAN_REVIEW_REQUESTED":
                    _remember_pending_plan_review(state, runtime_event)
        for runtime_event in _drain_runtime_event_sources(runtime_event_sources or []):
            state.runtime_events.append(runtime_event)
            yield runtime_source_event(runtime_event)
        if _is_final_answer_tool_event(event_type, event_data, state):
            for reasoning_event in _flush_pending_reasoning_events(
                state,
                session_id=session_id,
                stream_scope=stream_scope,
                source_event_type=event_type,
                agent_id=agent_id,
                agent_name=agent_name,
            ):
                yield reasoning_event
            if event_type == "TOOL_CALL_START":
                completion_error = _runtime_completion_error(runtime_event_sources or [])
                if completion_error:
                    state.subagent_completion_blocked_reason = completion_error
                    state.blocked_final_text = ""
                    continue
                state.final_answer_started = True
                yield runtime_trace_source_event(
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
        if event_type == "TOOL_CALL_START":
            for reasoning_event in _flush_pending_reasoning_events(
                state,
                session_id=session_id,
                stream_scope=stream_scope,
                source_event_type=event_type,
                agent_id=agent_id,
                agent_name=agent_name,
            ):
                yield reasoning_event
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
        next_tool_calls = _update_tool_calls(
            state.assistant_tool_calls,
            event_type,
            event_data,
        )
        if next_tool_calls is not state.assistant_tool_calls:
            state.assistant_tool_calls = next_tool_calls
        for skill_event in _skill_lifecycle_events(
            event_type=event_type,
            event_data=event_data,
            state=state,
            skill_runs=skill_runs,
            session_id=session_id,
            persist=persist,
            agent_id=agent_id,
            agent_name=agent_name,
        ):
            yield skill_event
        _record_tool_execution_artifacts(
            session_id=session_id,
            event_type=event_type,
            event_data=event_data,
            persist=persist,
            scope=stream_scope,
            agent_id=agent_id,
            agent_name=agent_name,
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
            yield runtime_trace_source_event(
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

        if event_type == "REQUIRE_EXTERNAL_EXECUTION" and _is_subagent_task_external_event(event_data):
            _collect_pending_tool_batch(pending_subagent_batches, event_type, event_data)
            continue

        if event_type in HITL_EVENT_TYPES:
            _collect_pending_tool_batch(pending_hitl_batches, event_type, event_data)
            continue

        if event_type == "TEXT_BLOCK_DELTA":
            delta = str(event_data.get("delta", ""))
            if state.subagent_completion_blocked_reason:
                state.blocked_final_text += delta
                continue
            if state.final_answer_started:
                state.assistant_text += delta
                yield runtime_trace_source_event(
                    "AGENT_FINAL_ANSWER_DELTA", session_id=session_id, scope=stream_scope,
                    source="runtime", source_event_type=event_type, agent_id=agent_id,
                    agent_name=agent_name, status="running", payload={"delta": delta},
                    collect=state.runtime_events,
                )
                yield source_event("FINAL_ANSWER_DELTA", {"delta": delta})
            else:
                state.pending_text += delta
            continue
        elif event_type == "THINKING_BLOCK_DELTA":
            state.assistant_thinking += str(event_data.get("delta", ""))

        if event_type == "REPLY_END" and skill_runs:
            completed_skill_runs = _set_skill_run_status(skill_runs, "completed")
            for skill in completed_skill_runs:
                yield runtime_trace_source_event(
                    "SKILL_COMPLETED",
                    session_id=session_id,
                    scope="skill",
                    agent_id=agent_id,
                    agent_name=agent_name,
                    status="completed",
                    payload=skill.model_dump(by_alias=True),
                    collect=state.runtime_events,
                )
            yield source_event("SKILL_END", {
                "skills": [item.model_dump(by_alias=True) for item in completed_skill_runs],
                "status": "completed",
            })

        if event_type == "REPLY_END" and state.pending_text:
            completion_error = _runtime_completion_error(runtime_event_sources or [])
            if completion_error:
                state.subagent_completion_blocked_reason = completion_error
                state.blocked_final_text = state.pending_text
                state.pending_text = ""
                continue
            if state.final_answer_started or not state.reply_had_tool_call:
                pending = state.pending_text
                state.pending_text = ""
                state.final_answer_started = True
                state.assistant_text += pending
                yield runtime_trace_source_event(
                    "AGENT_FINAL_ANSWER_STARTED", session_id=session_id, scope=stream_scope,
                    source="runtime", source_event_type=event_type, agent_id=agent_id,
                    agent_name=agent_name, status="running", reason="direct_text_response",
                    payload={"recovered": False, "direct": True}, collect=state.runtime_events,
                )
                yield runtime_trace_source_event(
                    "AGENT_FINAL_ANSWER_DELTA", session_id=session_id, scope=stream_scope,
                    source="runtime", source_event_type=event_type, agent_id=agent_id,
                    agent_name=agent_name, status="running", reason="direct_text_response",
                    payload={"delta": pending, "direct": True}, collect=state.runtime_events,
                )
                yield source_event("FINAL_ANSWER_DELTA", {"delta": pending})
            else:
                for reasoning_event in _flush_pending_reasoning_events(
                    state,
                    session_id=session_id,
                    stream_scope=stream_scope,
                    source_event_type=event_type,
                    agent_id=agent_id,
                    agent_name=agent_name,
                ):
                    yield reasoning_event

        runtime_event = runtime_event_from_agentscope_event(
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
        yield source_event(event_type, event_data)
        for runtime_event in _drain_runtime_event_sources(runtime_event_sources or []):
            state.runtime_events.append(runtime_event)
            yield runtime_source_event(runtime_event)
        if _is_pending_plan_review_ready(state, event_type, event_data):
            async for final_event in _stream_pending_plan_review_events(
                state,
                session_id=session_id,
                stream_scope=stream_scope,
                agent_id=agent_id,
                agent_name=agent_name,
            ):
                yield final_event
            return

    for runtime_event in _drain_runtime_event_sources(runtime_event_sources or []):
        state.runtime_events.append(runtime_event)
        yield runtime_source_event(runtime_event)

    if pending_subagent_batches:
        reply_ids = {str(batch["reply_id"]) for batch in pending_subagent_batches}
        if len(reply_ids) != 1:
            raise RuntimeError("同一轮 Subagent external execution 出现多个 reply_id")
        manager = await _find_subagent_manager(
            runtime_event_sources or [],
            agent=agent,
        )
        if manager is None:
            yield source_event("ERROR", {"message": "Subagent task executor is not available."})
            state.stopped = True
            state.stop_reason = "subagent_executor_missing"
            return
        tool_calls = [
            tool_call
            for batch in pending_subagent_batches
            for tool_call in batch["tool_calls"]
        ]
        execution_tasks = [
            asyncio.create_task(manager.execute_external_tool_call(tool_call))
            for tool_call in tool_calls
        ]
        try:
            while not all(task.done() for task in execution_tasks):
                for runtime_event in _drain_runtime_event_sources(runtime_event_sources or []):
                    state.runtime_events.append(runtime_event)
                    yield runtime_source_event(runtime_event)
                await asyncio.sleep(0.1)
            execution_results = [await task for task in execution_tasks]
        finally:
            for runtime_event in _drain_runtime_event_sources(runtime_event_sources or []):
                state.runtime_events.append(runtime_event)
                yield runtime_source_event(runtime_event)
        resume_event = ExternalExecutionResultEvent(
            reply_id=reply_ids.pop(),
            execution_results=execution_results,
        )
        mark_consumed = getattr(manager, "mark_results_consumed_for_tool_calls", None)
        if callable(mark_consumed):
            # 结果已经作为 ExternalExecutionResultEvent 交给父 Agent；
            # 不依赖恢复流是否能产出首个事件来确认消费，避免模型调用
            # 在首个事件前失败时重复触发 completion barrier。
            mark_consumed(tool_calls)
        continuation_started = False
        async for resumed_event in stream_leader_agent_reply(
            agent=agent,
            user_msg=resume_event,
            state=state,
            session_id=session_id,
            persist=persist,
            agent_id=agent_id,
            agent_name=agent_name,
            model_name=model_name,
            guardrails=guardrails,
            message=message,
            stream_scope=stream_scope,
            skill_runs=skill_runs,
            plan_mode_manager=plan_mode_manager,
            runtime_event_sources=runtime_event_sources,
            sandbox=sandbox,
        ):
            if not continuation_started:
                continuation_started = True
                for runtime_event in _drain_runtime_event_sources(runtime_event_sources or []):
                    state.runtime_events.append(runtime_event)
                    yield runtime_source_event(runtime_event)
            yield resumed_event
        for runtime_event in _drain_runtime_event_sources(runtime_event_sources or []):
            state.runtime_events.append(runtime_event)
            yield runtime_source_event(runtime_event)
        return

    if pending_hitl_batches:
        if state.active_skill_run and persist:
            state.active_skill_run = update_skill_run_status(
                state.active_skill_run,
                status="waiting_user",
                current_step="waiting_hitl_resume",
            )
        if persist:
            _append_assistant_message(
                session_id=session_id,
                agent_id=agent_id,
                agent_name=agent_name,
                model_name=model_name,
                content=state.assistant_text,
                error=None,
                thinking=state.assistant_thinking,
                skill_calls=skill_runs,
                tool_calls=state.assistant_tool_calls,
                model_caches=state.assistant_model_caches,
                runtime_events=state.runtime_events,
            )
        for batch in pending_hitl_batches:
            interaction = _create_pending_interaction(
                interaction_type=str(batch["type"]),
                agent=agent,
                session_id=session_id,
                agent_id=agent_id,
                agent_name=agent_name,
                model_name=model_name,
                reply_id=str(batch["reply_id"]),
                tool_calls=list(batch["tool_calls"]),
                # 这里的集合只来自 AgentScope 实际发出的 REQUIRE 事件，不能
                # 用 UI 工具状态猜测，否则会在 resume 时触发未知工具调用错误。
                resume_tool_calls=list(batch["tool_calls"]),
                message=message,
                assistant_text=state.assistant_text,
                assistant_tool_calls=state.assistant_tool_calls,
                skill_runs=skill_runs,
                scope=stream_scope,
                chat_data={
                    "sandbox": sandbox,
                    "sessionWorkspaceDir": state.session_workspace_dir,
                    "sessionArtifactPathsBefore": state.session_artifact_paths_before,
                },
                persist=persist,
            )
            wait_state = waiting_status_for_interaction(
                interaction.type,
                interaction.resume_tool_calls,
            )
            yield runtime_trace_source_event(
                "RUN_WAITING",
                session_id=session_id,
                scope="run",
                source="runtime",
                source_event_type=interaction.type,
                agent_id=agent_id,
                agent_name=agent_name,
                status=wait_state,
                reason=waiting_reason(wait_state),
                payload={
                    "interactionId": interaction.id,
                    "interactionType": interaction.type,
                    "waitState": wait_state,
                    "reason": waiting_reason(wait_state),
                },
                collect=state.runtime_events,
            )
            yield runtime_trace_source_event(
                "AGENT_WAITING_HUMAN",
                session_id=session_id,
                scope=stream_scope,
                source="runtime",
                source_event_type=interaction.type,
                agent_id=agent_id,
                agent_name=agent_name,
                status="waiting",
                reason="hitl_required",
                payload={"interactionId": interaction.id, "interactionType": interaction.type},
                collect=state.runtime_events,
            )
            yield source_event("HITL_REQUIRED", _hitl_payload(interaction))
        state.stopped = True
        state.stop_reason = "hitl"
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
                model_caches=state.assistant_model_caches,
                runtime_events=state.runtime_events,
                clarification_requests=[clarification],
            )
        yield runtime_trace_source_event(
            "RUN_WAITING",
            session_id=session_id,
            scope="run",
            agent_id=agent_id,
            agent_name=agent_name,
            status="waiting_user",
            reason="clarification_required",
            payload={
                "interactionId": str(clarification.get("id") or ""),
                "interactionType": "CLARIFICATION_REQUIRED",
                "waitState": "waiting_user",
                "reason": "clarification_required",
            },
            collect=state.runtime_events,
        )
        yield runtime_trace_source_event(
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
        yield source_event("CLARIFICATION_REQUIRED", clarification)
        state.stopped = True
        state.stop_reason = "clarification"
        return

    if state.subagent_completion_blocked_reason:
        if state.subagent_completion_block_count >= 4:
            raise RuntimeError(
                "父 Agent 连续尝试结束，但 attached Subagent 仍未完成消费: "
                f"{state.subagent_completion_blocked_reason}"
            )
        reason = state.subagent_completion_blocked_reason
        state.subagent_completion_blocked_reason = ""
        state.blocked_final_text = ""
        state.pending_text = ""
        state.final_answer_started = False
        state.subagent_completion_block_count += 1
        async for resumed_event in stream_leader_agent_reply(
            agent=agent,
            user_msg=UserMsg(
                name="system",
                content=(
                    "Your final answer was withheld by the attached Subagent completion barrier. "
                    f"{reason} Do not finalize again until every attached task is terminal and its "
                    "result has been read with SubagentResultRead."
                ),
            ),
            state=state,
            session_id=session_id,
            persist=persist,
            agent_id=agent_id,
            agent_name=agent_name,
            model_name=model_name,
            guardrails=guardrails,
            message=message,
            stream_scope=stream_scope,
            skill_runs=skill_runs,
            plan_mode_manager=plan_mode_manager,
            runtime_event_sources=runtime_event_sources,
            sandbox=sandbox,
        ):
            yield resumed_event
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


def _drain_runtime_event_sources(sources: list[object]) -> list[dict]:
    """从运行时增强模块中取出待发送 Runtime Event。"""

    events: list[dict] = []
    for source in sources:
        drain = getattr(source, "drain_runtime_events", None)
        if callable(drain):
            events.extend(drain())
    return events


def _runtime_completion_error(sources: list[object]) -> str:
    """收集运行时增强模块的成功收口屏障错误。"""

    for source in sources:
        completion_error = getattr(source, "completion_error", None)
        if callable(completion_error):
            error = str(completion_error() or "")
            if error:
                return error
    return ""


def _skill_lifecycle_events(
    *,
    event_type: str,
    event_data: dict,
    state: LeaderStreamState,
    skill_runs: list[ChatSkillRun],
    session_id: str,
    persist: bool,
    agent_id: str,
    agent_name: str,
) -> list[str]:
    """把 AgentScope Skill viewer 工具事件转换为平台 Skill 生命周期。"""

    tool_call_id = str(event_data.get("tool_call_id") or "")
    tool_call = next((item for item in state.assistant_tool_calls if item.id == tool_call_id), None)
    tool_name = str(event_data.get("tool_call_name") or getattr(tool_call, "name", ""))
    if tool_name != "Skill":
        return []

    if event_type == "TOOL_CALL_START":
        payload = {"id": tool_call_id, "name": "Skill", "status": "running"}
        return [runtime_trace_source_event(
            "SKILL_STARTED",
            session_id=session_id,
            scope="skill",
            source="agentscope",
            source_event_type=event_type,
            agent_id=agent_id,
            agent_name=agent_name,
            status="running",
            payload=payload,
            collect=state.runtime_events,
        )]

    if event_type != "TOOL_RESULT_END":
        return []

    requested_name = _skill_name_from_tool_call(tool_call)
    detail = next(
        (
            item for item in state.available_skill_details
            if str(item.get("name") or "") == requested_name
        ),
        None,
    )
    if detail is None and requested_name:
        resolved_skills, _missing = resolve_installed_skills([requested_name])
        resolved_details = list_skill_usage_details(resolved_skills)
        detail = resolved_details[0] if resolved_details else None
    failed = str(event_data.get("state") or "").lower() in {"error", "failed", "denied"} or detail is None
    if failed:
        payload = {
            "id": tool_call_id,
            "name": requested_name or "Skill",
            "status": "failed",
        }
        return [
            runtime_trace_source_event(
                "SKILL_FAILED",
                session_id=session_id,
                scope="skill",
                source="agentscope",
                source_event_type=event_type,
                agent_id=agent_id,
                agent_name=agent_name,
                status="failed",
                payload=payload,
                collect=state.runtime_events,
            ),
            source_event("SKILL_END", {"skills": [payload], "status": "failed"}),
        ]

    skill_run = ChatSkillRun.model_validate({**detail, "status": "running"})
    if all(item.id != skill_run.id for item in skill_runs):
        skill_runs.append(skill_run)
    if persist:
        skill = Skill.model_validate({
            **detail,
            "category": "runtime",
            "tags": [],
            "status": "installed",
            "enabled": True,
            "updatedAt": "",
        })
        state.active_skill_run = start_or_resume_skill_run(
            session_id=session_id,
            agent_id=agent_id,
            skill=skill,
            status="active",
            current_step="instructions_loaded",
        )
    payload = skill_run.model_dump(by_alias=True)
    return [
        runtime_trace_source_event(
            "SKILL_CONTEXT",
            session_id=session_id,
            scope="skill",
            source="agentscope",
            source_event_type=event_type,
            agent_id=agent_id,
            agent_name=agent_name,
            status="running",
            payload=payload,
            collect=state.runtime_events,
        ),
        source_event("SKILL_CONTEXT", {"skills": [payload]}),
    ]


def _skill_name_from_tool_call(tool_call: ChatToolRun | None) -> str:
    """从 Skill viewer 的流式 JSON 参数中提取 Skill 名称。"""

    if tool_call is None or not tool_call.args:
        return ""
    try:
        value = json.loads(tool_call.args)
    except json.JSONDecodeError:
        return ""
    return str(value.get("skill") or "") if isinstance(value, dict) else ""


SUBAGENT_TASK_TOOL_NAMES = {
    "DelegateTask",
    "SubagentTaskList",
    "SubagentTaskWait",
    "SubagentResultRead",
    "SubagentTaskCancel",
}


def _is_subagent_task_external_event(event_data: dict) -> bool:
    """判断 AgentScope 外部执行事件是否只包含 attached Subagent 工具。"""

    tool_calls = list(event_data.get("tool_calls") or [])
    return bool(tool_calls) and all(
        str(tool_call.get("name") or "") in SUBAGENT_TASK_TOOL_NAMES
        for tool_call in tool_calls
    )


async def _find_subagent_manager(
    sources: list[object],
    *,
    agent: object | None = None,
) -> object | None:
    """从 source 或当前 Agent Toolkit 找到 Subagent manager。"""

    for source in sources:
        if isinstance(source, SubagentDelegationManager) or callable(getattr(source, "execute_external_tool_call", None)):
            return source
    toolkit = getattr(agent, "toolkit", None)
    get_tool = getattr(toolkit, "get_tool", None)
    if callable(get_tool):
        delegate_tool = await get_tool("DelegateTask")
        manager = getattr(delegate_tool, "_manager", None)
        if isinstance(manager, SubagentDelegationManager) or callable(
            getattr(manager, "execute_external_tool_call", None),
        ):
            return manager
    return None


def _remember_pending_plan_review(state: LeaderStreamState, runtime_event: dict) -> None:
    """暂存已提交审核的计划，等待工具结果闭合后安全停止 Agent。"""

    state.pending_plan_review = True
    payload = runtime_event.get("payload") if isinstance(runtime_event.get("payload"), dict) else {}
    revision = int(payload.get("revision") or 0)
    state.pending_plan_review_answer = f"计划 Revision {revision} 已生成，等待你的确认。"


def _is_pending_plan_review_ready(
    state: LeaderStreamState,
    event_type: str,
    event_data: dict,
) -> bool:
    """判断 plan_request_review 工具结果是否闭合。"""

    if not state.pending_plan_review or event_type != "TOOL_RESULT_END":
        return False
    tool_call_id = str(event_data.get("tool_call_id") or "")
    tool_name = str(event_data.get("tool_call_name") or "")
    if not tool_name and tool_call_id:
        tool_call = next((item for item in state.assistant_tool_calls if item.id == tool_call_id), None)
        tool_name = tool_call.name if tool_call else ""
    return _is_plan_review_tool_name(tool_name)


def _is_plan_review_tool_name(tool_name: str) -> bool:
    normalized = tool_name.replace(" ", "").replace("_", "").lower()
    return normalized == "planrequestreview"


async def _stream_pending_plan_review_events(
    state: LeaderStreamState,
    *,
    session_id: str,
    stream_scope: str,
    agent_id: str,
    agent_name: str,
) -> AsyncGenerator[str, None]:
    """输出待审核计划摘要并结束当前起草 Run。"""

    plan_answer = state.pending_plan_review_answer
    state.pending_plan_review = False
    state.pending_plan_review_answer = ""
    state.stopped = True
    state.stop_reason = "plan_review_requested"
    if not plan_answer:
        return
    state.final_answer_started = True
    state.assistant_text += plan_answer
    deltas = [plan_answer[index:index + 120] for index in range(0, len(plan_answer), 120)]
    for index, delta in enumerate(deltas):
        yield source_event("FINAL_ANSWER_DELTA", {"delta": delta})
        yield runtime_trace_source_event(
            "AGENT_FINAL_ANSWER_DELTA",
            session_id=session_id,
            scope=stream_scope,
            source="runtime",
            source_event_type="PLAN_REVIEW_REQUESTED",
            agent_id=agent_id,
            agent_name=agent_name,
            status="completed",
            payload={"delta": delta},
            collect=state.runtime_events,
        )
        if index < len(deltas) - 1:
            await asyncio.sleep(_PLAN_FINAL_ANSWER_CHUNK_DELAY_SECONDS)
