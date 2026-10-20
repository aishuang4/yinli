"""Chat 工具调用状态转换。"""

import json
from typing import Any

from src.server.routes.chat.models import ChatToolRun


def _tool_result_status(event_data: dict) -> str:
    state = str(event_data.get("state") or "").lower()
    return "failed" if state in {"error", "failed"} else "completed"


def _stringify_tool_input(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except TypeError:
        return str(value)


def _pending_tool_calls_to_runs(
    pending_tool_calls: list[dict],
    existing_tool_calls: list[ChatToolRun] | None = None,
) -> list[ChatToolRun]:
    """把暂停中的 HITL 工具调用转换为历史记录。"""

    next_calls = [item.model_copy() for item in existing_tool_calls or []]
    seen_ids = {item.id for item in next_calls}
    for tool_call in pending_tool_calls:
        tool_call_id = str(tool_call.get("id") or "")
        if not tool_call_id or tool_call_id in seen_ids:
            continue
        next_calls.append(ChatToolRun(
            id=tool_call_id,
            name=str(tool_call.get("name") or "tool"),
            args=_stringify_tool_input(tool_call.get("input")),
            status="running",
        ))
        seen_ids.add(tool_call_id)
    return next_calls


def _update_tool_calls(
    tool_calls: list[ChatToolRun],
    event_type: str,
    event_data: dict,
) -> list[ChatToolRun]:
    """根据 AgentScope 事件更新工具调用记录。"""

    tool_call_id = str(event_data.get("tool_call_id") or "")
    if not tool_call_id:
        return tool_calls
    next_calls = [item.model_copy() for item in tool_calls]
    index = next((idx for idx, item in enumerate(next_calls) if item.id == tool_call_id), -1)
    if event_type == "TOOL_CALL_START":
        if index == -1:
            next_calls.append(ChatToolRun(
                id=tool_call_id,
                name=str(event_data.get("tool_call_name") or "tool"),
                status="calling",
            ))
        return next_calls
    if index == -1:
        next_calls.append(ChatToolRun(
            id=tool_call_id,
            name=str(event_data.get("tool_call_name") or "tool"),
            status="calling",
        ))
        index = len(next_calls) - 1
    current = next_calls[index]
    if event_type == "TOOL_CALL_DELTA":
        next_calls[index] = current.model_copy(update={"args": current.args + str(event_data.get("delta") or "")})
    elif event_type in {"TOOL_CALL_END", "TOOL_RESULT_START"}:
        complete = event_data.get("input") if event_data.get("input") is not None else event_data.get("arguments")
        next_calls[index] = current.model_copy(update={
            "status": "running", "args": _stringify_tool_input(complete) if complete is not None else current.args,
        })
    elif event_type == "TOOL_RESULT_TEXT_DELTA":
        next_calls[index] = current.model_copy(update={
            "result": current.result + str(event_data.get("delta") or ""),
            "status": "running",
        })
    elif event_type == "TOOL_RESULT_END":
        next_calls[index] = current.model_copy(update={"status": _tool_result_status(event_data)})
    else:
        return tool_calls
    return next_calls
