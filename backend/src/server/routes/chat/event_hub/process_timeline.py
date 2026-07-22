"""Process block 的流式时间线投影。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.server.routes.chat.event_hub.hitl_display import (
    ask_user_result_preview as _ask_user_result_preview,
    ask_user_tool_call_from_approval as _ask_user_tool_call_from_approval,
    clarification_tool_call_from_approval as _clarification_tool_call_from_approval,
    format_ask_user_question_preview as _format_ask_user_question_preview,
    hitl_process_item_from_required_event as _hitl_process_item_from_required_event,
)
from src.server.routes.chat.event_hub.run_state_mutations import (
    _find_approval,
    _find_tool,
    _skill_display_event_name,
    _tool_display_data,
    _tool_display_event_name,
)


PROCESS_TIMELINE_KEY = "_processTimelineItems"


def _project_process_timeline(state: dict[str, Any], event_type: str, data: dict[str, Any]) -> None:
    """维护 process block 的流式顺序。

    reasoning / thinking 是流式文本，只有相邻片段才合并；中间出现工具或
    skill 后，新文本必须成为新的 item，前端才能按 ReAct 顺序展示。
    """

    if event_type in {"REASONING_DELTA", "TEXT_BLOCK_DELTA"}:
        _append_process_text_timeline_item(
            state,
            event_name="reasoning.delta",
            target_prefix="reasoning",
            text=str(data.get("delta") or ""),
        )
        return
    if event_type == "THINKING_BLOCK_DELTA":
        _append_process_text_timeline_item(
            state,
            event_name="thinking.delta",
            target_prefix="thinking",
            text=str(data.get("delta") or ""),
        )
        return
    if event_type in {"TOOL_CALL_START", "TOOL_CALL_DELTA", "TOOL_CALL_END", "TOOL_RESULT_TEXT_DELTA", "TOOL_RESULT_END"}:
        _upsert_process_tool_timeline_item(state, data)
        return
    if event_type in {"SKILL_CONTEXT", "SKILL_END"}:
        _upsert_process_skill_timeline_items(state)


def _process_timeline_items(state: dict[str, Any]) -> list[dict[str, Any]]:
    items = state.get(PROCESS_TIMELINE_KEY)
    if not isinstance(items, list):
        items = []
    normalized = [item for item in items if isinstance(item, dict)]
    state[PROCESS_TIMELINE_KEY] = normalized
    return normalized


def _append_process_text_timeline_item(
    state: dict[str, Any],
    *,
    event_name: str,
    target_prefix: str,
    text: str,
) -> None:
    if not text:
        return
    items = _process_timeline_items(state)
    updated_at = str(state.get("updatedAt") or _utc_now())
    last_item = items[-1] if items else None
    if (
        isinstance(last_item, dict)
        and str(last_item.get("event") or "") == event_name
        and str(last_item.get("targetId") or "").startswith(f"{target_prefix}-")
    ):
        data = last_item.get("data") if isinstance(last_item.get("data"), dict) else {}
        current_text = str(data.get("text") or "")
        last_item["data"] = {**data, "text": current_text + text}
        last_item["status"] = "running"
        last_item["updatedAt"] = updated_at
        return

    target_id = f"{target_prefix}-{int(state.get('eventCount') or 0)}"
    items.append({
        "id": target_id,
        "event": event_name,
        "targetId": target_id,
        "status": "running",
        "createdAt": updated_at,
        "updatedAt": updated_at,
        "data": {"text": text},
    })


def _upsert_process_tool_timeline_item(state: dict[str, Any], data: dict[str, Any]) -> None:
    tool_id = str(data.get("tool_call_id") or data.get("toolCallId") or data.get("id") or "")
    if not tool_id:
        return
    tool = _find_tool(state, tool_id)
    if not tool:
        return
    item = {
        "id": str(tool.get("id") or tool_id),
        "event": _tool_display_event_name(tool),
        "targetId": str(tool.get("id") or tool_id),
        "status": str(tool.get("status") or "running"),
        "createdAt": str(tool.get("startedAt") or state.get("updatedAt") or _utc_now()),
        "updatedAt": str(tool.get("completedAt") or state.get("updatedAt") or _utc_now()),
        "data": _tool_display_data(tool),
    }
    _upsert_process_timeline_item(state, item)


def _upsert_process_skill_timeline_items(state: dict[str, Any]) -> None:
    for skill in state.get("skillCalls") or []:
        if not isinstance(skill, dict):
            continue
        skill_id = str(skill.get("id") or skill.get("name") or "")
        if not skill_id:
            continue
        _upsert_process_timeline_item(state, {
            "id": skill_id,
            "event": _skill_display_event_name(skill),
            "targetId": skill_id,
            "status": str(skill.get("status") or "running"),
            "createdAt": str(state.get("updatedAt") or _utc_now()),
            "updatedAt": str(state.get("updatedAt") or _utc_now()),
            "data": {
                "name": str(skill.get("name") or "Skill"),
                "description": str(skill.get("description") or ""),
                "trigger": str(skill.get("trigger") or ""),
                "content": str(skill.get("content") or ""),
                "resourcePath": str(skill.get("resourcePath") or ""),
                "resources": skill.get("resources") if isinstance(skill.get("resources"), list) else [],
            },
        })


def _upsert_hitl_process_timeline_item(state: dict[str, Any], data: dict[str, Any]) -> None:
    """把 AskUser 反问登记到过程时间线，便于折叠面板展示模型问题。"""

    item = _hitl_process_item_from_required_event(state, data)
    if not item:
        return
    _upsert_process_timeline_item(state, item)


def _complete_hitl_process_timeline_item(
    state: dict[str, Any],
    interaction_id: str,
    payload: dict[str, Any],
) -> None:
    """把用户提交给 AskUser 的答案写回过程时间线。"""

    approval = _find_approval(state, interaction_id)
    tool_call = _ask_user_tool_call_from_approval(approval)
    if not tool_call:
        tool_call = _clarification_tool_call_from_approval(approval)
    tool_id = str(tool_call.get("id") or "")
    if not tool_id:
        return
    item = {
        "id": tool_id,
        "event": "tool.completed" if str(payload.get("action") or "") == "approve" else "tool.failed",
        "targetId": tool_id,
        "status": "completed" if str(payload.get("action") or "") == "approve" else "failed",
        "createdAt": str(state.get("updatedAt") or _utc_now()),
        "updatedAt": str(state.get("updatedAt") or _utc_now()),
        "data": {
            "name": "AskUser",
            "inputPreview": _format_ask_user_question_preview(tool_call),
            "input": _format_ask_user_question_preview(tool_call),
            "outputPreview": _ask_user_result_preview(tool_id, payload),
            "output": _ask_user_result_preview(tool_id, payload),
            "outputSize": len(_ask_user_result_preview(tool_id, payload)),
            "outputTruncated": False,
            "error": "" if str(payload.get("action") or "") == "approve" else "用户取消或拒绝补充信息。",
        },
    }
    _upsert_process_timeline_item(state, item)


def _upsert_process_timeline_item(state: dict[str, Any], item: dict[str, Any]) -> None:
    items = _process_timeline_items(state)
    target_id = str(item.get("targetId") or item.get("id") or "")
    existing_index = next((
        index
        for index, current in enumerate(items)
        if str(current.get("targetId") or current.get("id") or "") == target_id
    ), -1)
    if existing_index == -1:
        items.append(item)
    else:
        current = items[existing_index]
        current_data = current.get("data") if isinstance(current.get("data"), dict) else {}
        incoming_data = item.get("data") if isinstance(item.get("data"), dict) else {}
        merged_data = {**current_data, **incoming_data}
        for key in ("inputPreview", "outputPreview", "error"):
            if str(incoming_data.get(key) or "") == "" and str(current_data.get(key) or "") != "":
                merged_data[key] = current_data[key]
        items[existing_index] = {**current, **item, "data": merged_data}


def _latest_process_text_target_id(state: dict[str, Any], event_name: str, fallback: str) -> str:
    for item in reversed(_process_timeline_items(state)):
        if str(item.get("event") or "") == event_name:
            return str(item.get("targetId") or item.get("id") or fallback)
    return fallback

def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
