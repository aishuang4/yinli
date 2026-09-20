"""最终答案恢复轮：处理模型漏调 FinalAnswerStart 的受限补救。"""

from __future__ import annotations

from typing import Any, AsyncGenerator

from agentscope._utils._common import _generate_id
from agentscope.message import Msg, UserMsg
from agentscope.tool import ToolChoice

from src.server.routes.chat.generation_snapshot import snapshot_model_input_async
from src.server.routes.chat.helpers import source_event
from src.server.routes.chat.models import ChatToolRun
from src.server.routes.chat.runtime_events import (
    runtime_event_from_agentscope_event,
    runtime_trace_source_event,
)
from src.server.routes.chat.trace_recorder import (
    _attach_provider_cache_usage,
    _model_cache_from_event,
    _record_agent_scope_event,
    _record_permission_decision,
)


async def stream_final_answer_recovery_events(
    *,
    agent: object,
    state: Any,
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
        for attr in (
            "_handle_incoming_messages",
            "_reasoning",
            "_get_last_msg",
            "_batch_tool_calls",
        )
    ):
        return

    recovery_prompt = build_final_answer_recovery_prompt(
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
                agent=agent,
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
                agent=agent,
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
                agent=agent,
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
        yield runtime_trace_source_event(
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

    last_msg = agent._get_last_msg()
    if last_msg is None:
        return
    tool_calls = last_msg.get_content_blocks("tool_call")
    if not tool_calls:
        return
    for batch in await agent._batch_tool_calls(tool_calls):
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
    agent: object,
    state: Any,
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
            yield runtime_trace_source_event(
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
            yield runtime_trace_source_event(
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
            yield source_event("FINAL_ANSWER_DELTA", {"delta": delta})
        return
    if event_type == "THINKING_BLOCK_DELTA":
        state.assistant_thinking += str(event_data.get("delta", ""))
        return

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
            event_data["observationInput"] = await snapshot_model_input_async(agent, model_name=model_name)
        except Exception:
            event_data["observationInput"] = {"model": model_name}
    yield source_event(event_type, event_data)


def build_final_answer_recovery_prompt(
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


def _is_final_answer_tool_event(event_type: str, event_data: dict, state: Any) -> bool:
    """判断 AgentScope 工具事件是否属于 FinalAnswerStart 阶段标记。"""

    tool_call_id = str(event_data.get("tool_call_id") or "")
    tool_name = str(event_data.get("tool_call_name") or "")
    normalized = tool_name.replace(" ", "").replace("_", "").lower()
    if event_type == "TOOL_CALL_START" and normalized.endswith("finalanswerstart"):
        if tool_call_id:
            state.final_answer_tool_call_ids.add(tool_call_id)
        return True
    return bool(
        tool_call_id
        and tool_call_id in state.final_answer_tool_call_ids
        and event_type.startswith("TOOL_")
    )
