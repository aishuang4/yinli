"""从 AgentScope 事件里拆出对用户可见的正文和思考增量。"""

from __future__ import annotations

from typing import Any

THINKING_EVENT_TYPES = {"THINKING_BLOCK_DELTA", "REASONING_DELTA"}


def message_text_delta(data: dict[str, Any]) -> tuple[str, str] | None:
    """返回 (kind, text)。kind 为 thinking 或 answer；无法识别则 None。"""

    event_type = str(data.get("type") or "")
    delta = str(data.get("delta") or "")
    if not delta:
        return None
    if event_type in THINKING_EVENT_TYPES:
        return "thinking", delta
    if event_type == "TEXT_BLOCK_DELTA":
        return "answer", delta
    return None
