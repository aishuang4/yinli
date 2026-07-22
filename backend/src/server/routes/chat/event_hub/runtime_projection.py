"""Runtime Event、Actor timeline 与 Subagent 工具状态投影。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.server.routes.chat.event_hub.run_state_mutations import _truncate
from src.server.routes.chat.event_hub.subagent_timeline import (
    is_subagent_runtime_event as _is_subagent_runtime_event,
    subagent_status_from_runtime_event as _subagent_status_from_runtime_event,
    subagent_timeline_item_from_runtime_event as _subagent_timeline_item_from_runtime_event,
)


TOOL_OUTPUT_PREVIEW_MAX_CHARS = 4_000
SUBAGENT_TIMELINE_MAX_ITEMS = 80
ACTOR_TIMELINE_KEY = "_actorTimelineItems"


def _actor_timeline_items(state: dict[str, Any], actor_id: str) -> list[dict[str, Any]]:
    timelines = state.get(ACTOR_TIMELINE_KEY)
    if not isinstance(timelines, dict):
        timelines = {}
    items = timelines.get(actor_id)
    if not isinstance(items, list):
        items = []
    normalized = [item for item in items if isinstance(item, dict)]
    timelines[actor_id] = normalized
    state[ACTOR_TIMELINE_KEY] = timelines
    return normalized


def _build_actor_items_from_timeline(state: dict[str, Any], actor_id: str, updated_at: str) -> list[dict[str, Any]]:
    if not actor_id:
        return []
    timelines = state.get(ACTOR_TIMELINE_KEY)
    if not isinstance(timelines, dict):
        return []
    items = timelines.get(actor_id)
    if not isinstance(items, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or item.get("targetId") or "")
        event_name = str(item.get("event") or "")
        if not item_id or not event_name:
            continue
        normalized.append({
            "id": item_id,
            "event": event_name,
            "targetId": str(item.get("targetId") or item_id),
            "status": str(item.get("status") or "running"),
            "createdAt": str(item.get("createdAt") or updated_at),
            "updatedAt": str(item.get("updatedAt") or updated_at),
            "data": item.get("data") if isinstance(item.get("data"), dict) else {},
        })
    return normalized


def _append_actor_text_timeline_item(
    state: dict[str, Any],
    actor_id: str,
    *,
    event_name: str,
    target_prefix: str,
    text: str,
) -> str:
    if not actor_id or not text:
        return ""
    items = _actor_timeline_items(state, actor_id)
    updated_at = str(state.get("updatedAt") or _utc_now())
    last_item = items[-1] if items else None
    if (
        isinstance(last_item, dict)
        and str(last_item.get("event") or "") == event_name
        and str(last_item.get("targetId") or "").startswith(f"{actor_id}:{target_prefix}-")
    ):
        data = last_item.get("data") if isinstance(last_item.get("data"), dict) else {}
        current_text = str(data.get("text") or "")
        last_item["data"] = {**data, "text": current_text + text}
        last_item["status"] = "running"
        last_item["updatedAt"] = updated_at
        return str(last_item.get("targetId") or "")

    target_id = f"{actor_id}:{target_prefix}-{int(state.get('eventCount') or 0)}"
    items.append({
        "id": target_id,
        "event": event_name,
        "targetId": target_id,
        "status": "running",
        "createdAt": updated_at,
        "updatedAt": updated_at,
        "data": {"text": text},
    })
    return target_id


def _upsert_actor_timeline_item(state: dict[str, Any], actor_id: str, item: dict[str, Any]) -> None:
    if not actor_id:
        return
    items = _actor_timeline_items(state, actor_id)
    target_id = str(item.get("targetId") or item.get("id") or "")
    existing_index = next((
        index
        for index, current in enumerate(items)
        if str(current.get("targetId") or current.get("id") or "") == target_id
    ), -1)
    if existing_index == -1:
        items.append(item)
    else:
        items[existing_index] = _merge_actor_timeline_item(items[existing_index], item)


def _merge_actor_timeline_item(current: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """合并 actor timeline item，避免终态 Runtime Event 覆盖真实工具名。"""

    current_data = current.get("data") if isinstance(current.get("data"), dict) else {}
    incoming_data = incoming.get("data") if isinstance(incoming.get("data"), dict) else {}
    merged_data = {**current_data, **incoming_data}
    for key in ("inputPreview", "outputPreview", "error"):
        if str(incoming_data.get(key) or "") == "" and str(current_data.get(key) or "") != "":
            merged_data[key] = current_data[key]
    if (
        str(incoming_data.get("name") or "") in {"", "tool"}
        and str(current_data.get("name") or "") not in {"", "tool"}
    ):
        merged_data["name"] = current_data["name"]
    return {**current, **incoming, "data": merged_data}


def _append_actor_item_data(
    state: dict[str, Any],
    actor_id: str,
    target_id: str,
    key: str,
    delta: str,
) -> None:
    if not actor_id or not target_id or not delta:
        return
    items = _actor_timeline_items(state, actor_id)
    for item in items:
        if str(item.get("targetId") or item.get("id") or "") != target_id:
            continue
        data = item.get("data") if isinstance(item.get("data"), dict) else {}
        item["data"] = {**data, key: str(data.get(key) or "") + delta}
        item["updatedAt"] = str(state.get("updatedAt") or _utc_now())
        item["status"] = "running"
        return


def _latest_actor_text_target_id(state: dict[str, Any], actor_id: str, event_name: str, fallback: str) -> str:
    for item in reversed(_actor_timeline_items(state, actor_id)):
        if str(item.get("event") or "") == event_name:
            return str(item.get("targetId") or item.get("id") or fallback)
    return fallback


def _project_actor_timeline_from_runtime_event(state: dict[str, Any], event: dict[str, Any]) -> None:
    """把单 Agent Runtime Event 投影到 actor 的 ReAct timeline。"""

    if _runtime_event_has_raw_main_stream_pair(event):
        return
    actor_id = _actor_id_from_runtime_event(state, event)
    if not actor_id:
        return
    runtime_type = str(event.get("type") or "")
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    status = str(event.get("status") or "running")
    updated_at = str(state.get("updatedAt") or _utc_now())

    if runtime_type == "AGENT_OUTPUT_DELTA":
        _append_actor_text_timeline_item(
            state,
            actor_id,
            event_name="reasoning.delta",
            target_prefix="reasoning",
            text=str(payload.get("delta") or ""),
        )
        return
    if runtime_type == "AGENT_THINKING":
        _append_actor_text_timeline_item(
            state,
            actor_id,
            event_name="thinking.delta",
            target_prefix="thinking",
            text=str(payload.get("delta") or ""),
        )
        return
    if runtime_type in {
        "TOOL_CALL_STARTED",
        "TOOL_CALL_DELTA",
        "TOOL_CALL_COMPLETED",
        "TOOL_RESULT_STARTED",
        "TOOL_RESULT_DELTA",
        "TOOL_RESULT_COMPLETED",
    }:
        tool_id = _runtime_tool_target_id(actor_id, payload)
        if not tool_id:
            return
        tool_name = str(payload.get("tool_call_name") or payload.get("toolName") or "tool")
        if runtime_type == "TOOL_CALL_DELTA":
            _append_actor_item_data(state, actor_id, tool_id, "inputPreview", str(payload.get("delta") or ""))
            return
        if runtime_type == "TOOL_RESULT_DELTA":
            _append_actor_item_data(state, actor_id, tool_id, "outputPreview", str(payload.get("delta") or ""))
            return
        event_name = "tool.started"
        item_status = "running"
        if runtime_type == "TOOL_RESULT_COMPLETED":
            item_status = "failed" if status == "failed" else "completed"
            event_name = "tool.failed" if item_status == "failed" else "tool.completed"
        _upsert_actor_timeline_item(state, actor_id, {
            "id": tool_id,
            "event": event_name,
            "targetId": tool_id,
            "status": item_status,
            "createdAt": updated_at,
            "updatedAt": updated_at,
            "data": {
                "name": tool_name,
                "inputPreview": "",
                "outputPreview": "",
                "outputSize": 0,
                "outputTruncated": False,
                "error": str(payload.get("state") or "") if item_status == "failed" else "",
            },
        })


def _project_subagent_tool_from_runtime_event(state: dict[str, Any], event: dict[str, Any]) -> None:
    """把 Subagent 委托 Runtime Event 合并到父 DelegateTask 工具项。"""

    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    tool_id = str(payload.get("toolCallId") or payload.get("tool_call_id") or "")
    invocation_id = str(payload.get("invocationId") or "")
    if not tool_id and invocation_id:
        tool_id = f"subagent-{invocation_id}"
    if not tool_id:
        return
    runtime_type = str(event.get("type") or "")
    if not _is_subagent_runtime_event(runtime_type, payload):
        return

    tools = list(state.get("tools") or [])
    index = next((idx for idx, item in enumerate(tools) if isinstance(item, dict) and item.get("id") == tool_id), -1)
    current = tools[index] if index >= 0 and isinstance(tools[index], dict) else {}
    timeline = [
        item
        for item in (current.get("subagentTimeline") or [])
        if isinstance(item, dict)
    ]
    timeline_item = _subagent_timeline_item_from_runtime_event(event)
    if timeline_item:
        timeline.append(timeline_item)

    subagent_name = str(payload.get("subagentName") or current.get("subagentName") or "Subagent")
    if payload.get("subagentFinalAnswerDelta"):
        summary = str(current.get("subagentSummary") or "") + str(payload.get("delta") or "")
    else:
        summary = str(
            payload.get("resultPreview")
            or payload.get("partialResultPreview")
            or current.get("subagentSummary")
            or ""
        )
    error = str(payload.get("error") or current.get("subagentError") or "")
    status = _subagent_status_from_runtime_event(runtime_type, str(event.get("status") or current.get("subagentStatus") or "running"))
    output_preview = summary or str(current.get("outputPreview") or "")
    if error and not output_preview:
        output_preview = error

    tool = {
        **current,
        "id": tool_id,
        "name": str(current.get("name") or "DelegateTask"),
        "status": status,
        "inputPreview": str(current.get("inputPreview") or payload.get("taskPreview") or ""),
        "outputPreview": _truncate(output_preview, TOOL_OUTPUT_PREVIEW_MAX_CHARS),
        "outputSize": max(int(current.get("outputSize") or 0), len(output_preview)),
        "outputTruncated": len(output_preview) > TOOL_OUTPUT_PREVIEW_MAX_CHARS,
        "error": error,
        "startedAt": str(current.get("startedAt") or event.get("timestamp") or _utc_now()),
        "completedAt": str(current.get("completedAt") or ""),
        "subagentName": subagent_name,
        "subagentInvocationId": invocation_id or str(current.get("subagentInvocationId") or ""),
        "subagentStatus": status,
        "subagentTimeline": timeline[-SUBAGENT_TIMELINE_MAX_ITEMS:],
        "subagentSummary": summary,
        "subagentError": error,
    }
    if status in {"completed", "failed", "waiting"}:
        tool["completedAt"] = str(event.get("timestamp") or _utc_now())
    if index >= 0:
        tools[index] = tool
    else:
        tools.append(tool)
    state["tools"] = tools[-200:]


def _actor_id_from_runtime_event(state: dict[str, Any], event: dict[str, Any]) -> str:
    scope = str(event.get("scope") or "")
    if scope not in {"single", "single_agent"}:
        return ""
    return _default_actor_id(state)


def _runtime_tool_target_id(actor_id: str, payload: dict[str, Any]) -> str:
    tool_call_id = str(payload.get("tool_call_id") or payload.get("toolCallId") or "")
    return f"{actor_id}:tool:{tool_call_id}" if tool_call_id else ""


def _runtime_event_has_raw_main_stream_pair(event: dict[str, Any]) -> bool:
    """判断 Runtime Event 是否已有主 Agent 原始事件负责展示。

    单 Agent 的 AgentScope 事件会紧跟着以原始事件进入 RunState；如果 Runtime
    Event 也投影到 actor timeline，会生成同一 tool_call_id 的第二个过程项。
    """

    scope = str(event.get("scope") or "")
    if scope not in {"single", "single_agent"}:
        return False
    runtime_type = str(event.get("type") or "")
    return runtime_type in {
        "AGENT_OUTPUT_DELTA",
        "AGENT_REASONING_DELTA",
        "AGENT_THINKING",
        "TOOL_CALL_STARTED",
        "TOOL_CALL_DELTA",
        "TOOL_CALL_COMPLETED",
        "TOOL_RESULT_STARTED",
        "TOOL_RESULT_DELTA",
        "TOOL_RESULT_COMPLETED",
    }


def _default_actor_id(state: dict[str, Any]) -> str:
    """返回当前单 Agent actor id。"""

    agent_id = str(state.get("agentId") or "")
    return f"single-{agent_id}" if agent_id else "single-agent"

def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
