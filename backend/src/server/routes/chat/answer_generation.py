"""一次模型生成的正文累计与终态判定。

循环仍由 AgentScope 掌握：有工具调用就继续，没有工具调用才结束 reply。
这里只记录「这一次模型生成」的正文，供投影和终态信号使用。
"""

from __future__ import annotations

from typing import Any

from src.server.routes.chat.helpers import source_event
from src.server.routes.chat.projection.source_events import SourceEvent
from src.server.routes.chat.runtime_events import runtime_trace_source_event


def begin_model_generation(state: Any) -> int:
    """开始下一次模型生成，并清掉上一次生成的正文与工具标记。"""

    generation = int(getattr(state, "reply_generation", 0) or 0) + 1
    state.reply_generation = generation
    state.generation_text = ""
    state.generation_had_tool = False
    return generation


def append_generation_text(state: Any, delta: str) -> int:
    """把正文增量记入当前生成。生成编号尚未出现时从 1 开始。"""

    generation = int(getattr(state, "reply_generation", 0) or 0)
    if generation <= 0:
        # 正文先于 MODEL_CALL_START 到达时补一个生成号。
        # 工具标记若已经写下，保持不动，避免把同一次生成误判成最终答案。
        generation = 1
        state.reply_generation = generation
        state.generation_text = ""
    state.generation_text = str(getattr(state, "generation_text", "") or "") + delta
    state.assistant_reasoning_text = str(getattr(state, "assistant_reasoning_text", "") or "") + delta
    return generation


def mark_generation_tool(state: Any) -> None:
    """当前生成出现了工具调用，这段正文不能成为最终答案。"""

    state.generation_had_tool = True
    state.reply_had_tool_call = True


def clean_generation_text(state: Any) -> str:
    """这次生成没有工具调用且正文非空时，返回可作为最终答案的原文。"""

    if getattr(state, "generation_had_tool", False):
        return ""
    text = str(getattr(state, "generation_text", "") or "")
    if not text.strip():
        return ""
    generation = int(getattr(state, "reply_generation", 0) or 0)
    if generation <= 0:
        return ""
    return text


def answer_ready_events(
    state: Any,
    *,
    session_id: str,
    stream_scope: str,
    source_event_type: str,
    agent_id: str,
    agent_name: str,
    reason: str,
    recovered: bool = False,
) -> list[SourceEvent]:
    """这次生成没有工具调用时，把已流式正文标成最终答案。不再重发正文 delta。"""

    text = clean_generation_text(state)
    if not text:
        return []
    generation = int(state.reply_generation)
    state.assistant_text = text
    state.final_answer_started = True
    payload: dict[str, Any] = {"generation": generation, "direct": True}
    if recovered:
        payload["recovered"] = True
    return [
        runtime_trace_source_event(
            "AGENT_FINAL_ANSWER_STARTED",
            session_id=session_id,
            scope=stream_scope,
            source="runtime",
            source_event_type=source_event_type,
            agent_id=agent_id,
            agent_name=agent_name,
            status="running",
            reason=reason,
            payload=payload,
            collect=state.runtime_events,
        ),
        runtime_trace_source_event(
            "AGENT_FINAL_ANSWER_DELTA",
            session_id=session_id,
            scope=stream_scope,
            source="runtime",
            source_event_type=source_event_type,
            agent_id=agent_id,
            agent_name=agent_name,
            status="running",
            reason=reason,
            payload={"delta": text, "generation": generation, "direct": True},
            collect=state.runtime_events,
        ),
        source_event("ANSWER_READY", {"generation": generation}),
    ]
