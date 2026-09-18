"""把 Agent 上下文快照成 generation Observation 的 input/output。"""

from __future__ import annotations

import json
from typing import Any

MAX_SYSTEM_CHARS = 8000
MAX_SUMMARY_CHARS = 2000
MAX_BLOCK_CHARS = 4000
MAX_MESSAGES = 24
MAX_BLOCKS = 16


def _clip(text: str, limit: int) -> str:
    """截断长文本，保留换行，方便对照真实送模内容。"""

    value = str(text or "")
    if len(value) <= limit:
        return value
    return value[:limit] + "..."


def snapshot_model_input(
    agent: object,
    *,
    model_name: str = "",
    extra_message: object | None = None,
) -> dict[str, Any]:
    """读取即将发给模型的 system / summary / context，供 Trace 调优。"""

    model = getattr(agent, "model", None)
    resolved = (
        model_name
        or str(getattr(model, "model_name", "") or "")
        or str(getattr(model, "model", "") or "")
    )
    system = getattr(agent, "_system_prompt", None)
    state = getattr(agent, "state", None)
    summary = getattr(state, "summary", "") if state is not None else ""
    context = list(getattr(state, "context", []) or []) if state is not None else []
    if extra_message is not None and extra_message not in context:
        context = [*context, extra_message]
    return {
        "model": resolved,
        "system": _clip(str(system or ""), MAX_SYSTEM_CHARS),
        "summary": _clip(_summary_text(summary), MAX_SUMMARY_CHARS),
        # HintBlock 在 context 里常挂在 assistant 上，送模时会被 formatter 拆成 user 消息。
        "messages": _serialize_messages(context)[-MAX_MESSAGES:],
    }


def generation_output_payload(
    *,
    text: str = "",
    thinking: str = "",
    tool_calls: list[dict[str, Any]] | None = None,
    usage: dict[str, Any] | None = None,
    finished_reason: str = "completed",
) -> dict[str, Any]:
    """模型一步的产出：reasoning / 文本 / 工具声明 / token。"""

    payload: dict[str, Any] = {
        "text": text or "",
        "thinking": thinking or "",
        "toolCalls": tool_calls or [],
        "finishedReason": finished_reason or "completed",
        "usage": usage or {},
    }
    return payload


def merge_generation_output(current: dict[str, Any] | None, incoming: dict[str, Any] | None) -> dict[str, Any]:
    """后写覆盖，但空值不抹掉已有 text / thinking / toolCalls / usage。"""

    base = dict(current or {})
    nxt = dict(incoming or {})
    for key in ("text", "thinking", "finishedReason"):
        value = nxt.get(key)
        if isinstance(value, str) and value:
            base[key] = value
        elif key not in base and value is not None:
            base[key] = value
    incoming_calls = nxt.get("toolCalls")
    if isinstance(incoming_calls, list) and incoming_calls:
        base["toolCalls"] = incoming_calls
    elif "toolCalls" not in base:
        base["toolCalls"] = incoming_calls if isinstance(incoming_calls, list) else []
    incoming_usage = nxt.get("usage")
    if isinstance(incoming_usage, dict) and incoming_usage:
        base["usage"] = {**(base.get("usage") if isinstance(base.get("usage"), dict) else {}), **incoming_usage}
    elif "usage" not in base:
        base["usage"] = incoming_usage if isinstance(incoming_usage, dict) else {}
    return base


def tool_output_text(output: Any) -> str:
    """把工具结果从字符串 / block 列表 / dict 抽成纯文本。"""

    if output is None:
        return ""
    if isinstance(output, str):
        return output
    if isinstance(output, dict):
        nested = output.get("text") or output.get("output") or output.get("content") or output.get("result")
        if nested is not None and nested is not output:
            return tool_output_text(nested)
        return ""
    parts: list[str] = []
    for item in output or []:
        if isinstance(item, str):
            parts.append(item)
        else:
            parts.append(str(_field(item, "text") or _field(item, "output") or ""))
    return "".join(parts)


def parse_tool_arguments(raw: Any) -> Any:
    """工具参数可能是 JSON 字符串。"""

    if isinstance(raw, (dict, list)):
        return raw
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return text
    return parsed


def _summary_text(summary: Any) -> str:
    if isinstance(summary, str):
        return summary
    parts: list[str] = []
    for item in summary or []:
        parts.append(str(getattr(item, "text", "") or item))
    return "".join(parts)


def _field(value: Any, key: str, default: Any = "") -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _serialize_messages(context: list[Any]) -> list[dict[str, Any]]:
    """按送模形态展开：HintBlock 变成独立 user 消息，不再标成 hint 类型。"""

    messages: list[dict[str, Any]] = []
    for item in context:
        messages.extend(_serialize_message(item))
    return messages


def _serialize_message(msg: Any) -> list[dict[str, Any]]:
    role = str(_field(msg, "role") or "") or "user"
    name = str(_field(msg, "name") or "")
    content = _field(msg, "content", None)
    if isinstance(content, str):
        return [{"role": role, "name": name, "text": _clip(content, MAX_BLOCK_CHARS)}]

    messages: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []

    def flush_pending() -> None:
        if not pending:
            return
        messages.append({"role": role, "name": name, "blocks": pending[:]})
        pending.clear()

    for block in list(content or [])[:MAX_BLOCKS]:
        if str(_field(block, "type") or "") == "hint":
            flush_pending()
            text = _clip(_hint_text(_field(block, "hint")), MAX_BLOCK_CHARS)
            if text:
                messages.append({"role": "user", "text": text})
            continue
        serialized = _serialize_block(block)
        if serialized:
            pending.append(serialized)
    flush_pending()
    return messages


def _serialize_block(block: Any) -> dict[str, Any] | None:
    kind = str(_field(block, "type") or "")
    if kind == "text":
        return {"type": "text", "text": _clip(str(_field(block, "text") or ""), MAX_BLOCK_CHARS)}
    if kind == "thinking":
        return {"type": "thinking", "text": _clip(str(_field(block, "thinking") or ""), MAX_BLOCK_CHARS)}
    if kind == "tool_call":
        return {
            "type": "tool_call",
            "id": str(_field(block, "id") or ""),
            "name": str(_field(block, "name") or ""),
            "input": parse_tool_arguments(_field(block, "input")),
        }
    if kind == "tool_result":
        return {
            "type": "tool_result",
            "id": str(_field(block, "id") or ""),
            "name": str(_field(block, "name") or ""),
            "output": _clip(_tool_result_text(_field(block, "output")), MAX_BLOCK_CHARS),
            "state": str(_field(block, "state") or ""),
        }
    if kind == "data":
        source = _field(block, "source", None)
        return {
            "type": "data",
            "name": str(_field(block, "name") or ""),
            "mediaType": str(_field(source, "media_type") or _field(source, "mediaType") or ""),
        }
    if kind:
        return {"type": kind}
    return None


def _hint_text(hint: Any) -> str:
    if isinstance(hint, str):
        return hint
    parts: list[str] = []
    for item in hint or []:
        parts.append(str(getattr(item, "text", "") or ""))
    return "".join(parts)


def _tool_result_text(output: Any) -> str:
    return tool_output_text(output)
