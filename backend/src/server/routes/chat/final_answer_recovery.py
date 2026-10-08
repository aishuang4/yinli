"""最终答案恢复轮：收尾正文为空时再要一轮纯文本。"""

from __future__ import annotations

from typing import Any, AsyncGenerator

from agentscope._utils._common import _generate_id
from agentscope.message import Msg, UserMsg
from agentscope.tool import ToolChoice

from src.server.routes.chat.answer_generation import (
    answer_ready_events,
    append_generation_text,
    begin_model_generation,
    mark_generation_tool,
)
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
    """回复结束时没有终态正文时，再要一轮不调用工具的正文。"""

    state.final_answer_recovery_attempted = True
    if not all(hasattr(agent, attr) for attr in ("_handle_incoming_messages", "_reasoning")):
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
        async for event in agent._reasoning(tool_choice=ToolChoice(mode="none")):
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
        for ready_event in answer_ready_events(
            state,
            session_id=session_id,
            stream_scope=stream_scope,
            source_event_type="FINAL_ANSWER_RECOVERY",
            agent_id=agent_id,
            agent_name=agent_name,
            reason="recover_missing_final_answer",
            recovered=True,
        ):
            yield ready_event
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
    if event_type == "MODEL_CALL_START":
        begin_model_generation(state)
    elif event_type in {"TOOL_CALL_START", "TOOL_CALL_DELTA", "TOOL_CALL_END"}:
        mark_generation_tool(state)

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
        generation = append_generation_text(state, delta)
        yield source_event("FINAL_ANSWER_DELTA", {"delta": delta, "generation": generation})
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
            else "The previous response ended without a user-facing answer, so its text stayed process notes."
        ),
        "Do not perform new investigation and do not call any tool.",
        "Write the final user-facing answer now as normal assistant text.",
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



