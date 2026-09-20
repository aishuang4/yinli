"""Leader Agent 事件流处理。"""

import asyncio
from dataclasses import dataclass, field
from typing import AsyncGenerator

from agentscope.event import ExternalExecutionResultEvent
from agentscope.message import UserMsg

from src.server.routes.chat.generation_snapshot import snapshot_model_input_async
from src.server.routes.chat.helpers import _set_skill_run_status, source_event
from src.server.routes.chat.trace_recorder import (
    apply_model_call_token_usage,
    build_model_call_context_stats_async,
    record_context_compression_events,
)
from src.server.routes.chat.hitl import _create_pending_interaction, _hitl_payload
from src.server.routes.chat.hitl_unattended import (
    MAX_UNATTENDED_TOOL_DENIALS,
    hitl_unattended_resolve,
)
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
    agentscope_reply_error_message,
    agentscope_reply_finished_reason,
    runtime_event_from_agentscope_event,
    runtime_trace_source_event,
    runtime_source_event,
)
from src.server.routes.chat.projection.source_events import SourceEvent
from src.server.routes.chat.skill_runs import update_skill_run_status
from src.server.routes.chat.leader_skill_lifecycle import (
    skill_lifecycle_events as _skill_lifecycle_events,
    skill_name_from_tool_call as _skill_name_from_tool_call,
)
from src.server.routes.chat.leader_subagent_support import (
    find_subagent_manager as _find_subagent_manager,
    is_subagent_task_external_event as _is_subagent_task_external_event,
)
from src.server.routes.chat.leader_plan_review import (
    is_pending_plan_review_ready as _is_pending_plan_review_ready,
    remember_pending_plan_review as _remember_pending_plan_review,
    stream_pending_plan_review_events as _stream_pending_plan_review_events,
)
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
    reply_finished_reason: str = "completed"
    reply_error: str = ""
    reply_had_thinking: bool = False
    trigger_source: str = "user"
    unattended_denials: int = 0


def _fill_tool_result_output(event_data: dict, tool_calls: list[ChatToolRun]) -> None:
    """AgentScope TOOL_RESULT_END 常常只有 state，stdout 在 delta 里。"""

    if event_data.get("output") not in (None, "", []) or event_data.get("result") not in (None, ""):
        return
    call_id = str(event_data.get("tool_call_id") or "")
    match = next((item for item in tool_calls if item.id == call_id), None)
    if match and match.result:
        event_data["output"] = match.result


def _fill_tool_call_input(event_data: dict, tool_calls: list[ChatToolRun]) -> None:
    """AgentScope TOOL_CALL_END 可能只有 delta 累计参数，补到 Observation。"""

    if event_data.get("input") not in (None, "") or event_data.get("arguments") not in (None, ""):
        return
    call_id = str(event_data.get("tool_call_id") or "")
    match = next((item for item in tool_calls if item.id == call_id), None)
    if match and match.args:
        event_data["input"] = match.args


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


# 直接回答提前流式投影的文本阈值（字符）。工具调用前的过程说明通常只有一两句，
# 超过该长度且本次 reply 还没有任何工具调用时，按最终回答处理。见 docs/chat-sse-event-contract.md §6。
DIRECT_ANSWER_STREAM_THRESHOLD = 600


def _should_stream_direct_answer(state: LeaderStreamState, runtime_event_sources: list[object]) -> bool:
    """本次 reply 尚无业务工具调用且文本足够长时，把累计文本视为直接回答开始流式投影。"""

    if state.reply_had_tool_call or state.final_answer_started or state.subagent_completion_blocked_reason:
        return False
    if len(state.pending_text) < DIRECT_ANSWER_STREAM_THRESHOLD:
        return False
    return not _runtime_completion_error(runtime_event_sources)


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
    deep_thinking: bool = False,
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
            state.reply_finished_reason = "completed"
            state.reply_error = ""
            state.reply_had_thinking = False
        elif event_type in {"TOOL_CALL_START", "TOOL_CALL_DELTA", "TOOL_CALL_END"}:
            state.reply_had_tool_call = True
        elif event_type == "REPLY_END":
            state.reply_finished_reason = agentscope_reply_finished_reason(event_data)
            state.reply_error = agentscope_reply_error_message(event_data)
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
            observation_text = (
                state.assistant_reasoning_text
                or state.assistant_thinking
                or state.pending_text
                or ""
            ).strip()
            if observation_text:
                event_data["observationText"] = observation_text

        stats: dict = {}
        if event_type in {"MODEL_CALL_START", "MODEL_CALL_END"}:
            # 先把 AgentScope 刚完成的压缩写成 trace，再统计本次调用的上下文规模。
            record_context_compression_events(
                agent=agent,
                session_id=session_id,
                persist=persist,
                scope=stream_scope,
                agent_id=agent_id,
                agent_name=agent_name,
            )
            try:
                stats = await build_model_call_context_stats_async(agent)
            except Exception:
                stats = {}
            if event_type == "MODEL_CALL_END":
                apply_model_call_token_usage(stats, event_data)
        _record_agent_scope_event(
            session_id=session_id,
            event_type=event_type,
            event_data=event_data,
            persist=persist,
            scope=stream_scope,
            agent_id=agent_id,
            agent_name=agent_name,
            tool_calls=state.assistant_tool_calls,
            agent=agent,
            context_stats=stats or None,
        )
        if stats:
            yield source_event("CONTEXT_USAGE", stats)
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
                if _should_stream_direct_answer(state, runtime_event_sources or []):
                    # 本次 reply 没有任何业务工具调用、文本已足够长：按契约视为直接回答，
                    # 提前开始流式投影，省掉 FinalAnswerStart 那一次模型往返。
                    pending = state.pending_text
                    state.pending_text = ""
                    state.final_answer_started = True
                    state.assistant_text += pending
                    yield runtime_trace_source_event(
                        "AGENT_FINAL_ANSWER_STARTED", session_id=session_id, scope=stream_scope,
                        source="runtime", source_event_type=event_type, agent_id=agent_id,
                        agent_name=agent_name, status="running", reason="direct_text_stream",
                        payload={"recovered": False, "direct": True, "optimistic": True},
                        collect=state.runtime_events,
                    )
                    yield runtime_trace_source_event(
                        "AGENT_FINAL_ANSWER_DELTA", session_id=session_id, scope=stream_scope,
                        source="runtime", source_event_type=event_type, agent_id=agent_id,
                        agent_name=agent_name, status="running", reason="direct_text_stream",
                        payload={"delta": pending, "direct": True}, collect=state.runtime_events,
                    )
                    yield source_event("FINAL_ANSWER_DELTA", {"delta": pending})
            continue
        elif event_type == "THINKING_BLOCK_DELTA":
            state.reply_had_thinking = True
            state.assistant_thinking += str(event_data.get("delta", ""))

        reply_completed = state.reply_finished_reason == "completed"
        if event_type == "REPLY_END" and skill_runs and reply_completed:
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

        if event_type == "REPLY_END" and state.pending_text and not reply_completed:
            for reasoning_event in _flush_pending_reasoning_events(
                state,
                session_id=session_id,
                stream_scope=stream_scope,
                source_event_type=event_type,
                agent_id=agent_id,
                agent_name=agent_name,
            ):
                yield reasoning_event

        if event_type == "REPLY_END" and state.pending_text and reply_completed:
            completion_error = _runtime_completion_error(runtime_event_sources or [])
            if completion_error:
                state.subagent_completion_blocked_reason = completion_error
                state.blocked_final_text = state.pending_text
                state.pending_text = ""
                continue
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
        if event_type == "MODEL_CALL_START":
            try:
                event_data["observationInput"] = await snapshot_model_input_async(
                    agent,
                    model_name=model_name,
                    extra_message=user_msg,
                )
            except Exception:
                event_data["observationInput"] = {"model": model_name}
        if event_type == "TOOL_CALL_END":
            _fill_tool_call_input(event_data, state.assistant_tool_calls)
        if event_type == "TOOL_RESULT_END":
            _fill_tool_result_output(event_data, state.assistant_tool_calls)
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
            deep_thinking=deep_thinking,
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

    if pending_hitl_batches and state.trigger_source == "scheduled_job":
        async for event in _resume_unattended_hitl(
            pending_hitl_batches,
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
            skill_runs=skill_runs,
            plan_mode_manager=plan_mode_manager,
            runtime_event_sources=runtime_event_sources,
            deep_thinking=deep_thinking,
            sandbox=sandbox,
        ):
            yield event
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
                    "deepThinking": deep_thinking,
                    "sessionWorkspaceDir": state.session_workspace_dir,
                    "sessionArtifactPathsBefore": state.session_artifact_paths_before,
                },
                plan_mode_manager=plan_mode_manager,
                runtime_event_sources=runtime_event_sources,
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

    clarification = None if state.trigger_source == "scheduled_job" else (
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
            deep_thinking=deep_thinking,
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
        state.reply_finished_reason == "completed"
        and not state.final_answer_started
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


async def _resume_unattended_hitl(
    pending_hitl_batches: list[dict],
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
    skill_runs: list[ChatSkillRun],
    plan_mode_manager: object | None,
    runtime_event_sources: list[object] | None,
    deep_thinking: bool,
    sandbox: bool,
) -> AsyncGenerator[str, None]:
    """cwd Write/Edit 批准、其余拒绝，禁止落入 waiting_* / PENDING_INTERACTIONS。"""

    reply_ids = {str(batch["reply_id"]) for batch in pending_hitl_batches}
    types = {str(batch["type"]) for batch in pending_hitl_batches}
    denied_names: list[str] = []
    resume_event = None
    for batch in pending_hitl_batches:
        denied, resume_event = hitl_unattended_resolve(
            str(batch["type"]),
            {"reply_id": batch["reply_id"], "tool_calls": list(batch["tool_calls"])},
            workspace_dir=state.session_workspace_dir,
        )
        denied_names.extend(denied)
    state.unattended_denials += len(denied_names)
    mixed_pause = len(reply_ids) != 1 or len(types) != 1 or resume_event is None
    if mixed_pause or state.unattended_denials > MAX_UNATTENDED_TOOL_DENIALS:
        state.stopped = True
        state.stop_reason = "hitl_denied"
        return
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
        deep_thinking=deep_thinking,
        sandbox=sandbox,
    ):
        yield resumed_event


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
