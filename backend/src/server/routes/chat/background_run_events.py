"""后台 Chat Run 的 SSE 解析、状态判定与 RunState Patch 编码。"""

from __future__ import annotations

import json
from typing import Any

from src.server.routes.chat.event_types import SSE_EVENT_RUNTIME, SSE_EVENT_TEAM
from src.server.routes.chat.event_hub import initial_run_state, normalize_run_state
from src.server.routes.chat.helpers import _sse_event
from src.server.routes.chat.lifecycle import is_waiting_run_status, stage_for_run_status
from src.server.routes.chat.run_state_schema import dump_run_state, dump_run_state_patch


RUNTIME_RUNNING_TYPES = {
    "RUN_STARTED",
    "RUN_RESUMED",
    "AGENT_STARTED",
    "AGENT_RESUMED",
}
RUNTIME_TERMINAL_STATUS = {
    "RUN_COMPLETED": "completed",
    "RUN_FAILED": "failed",
    "RUN_CANCELLED": "cancelled",
}
RUN_STATE_METADATA_PATCH_EVENTS = {
    "RUN",
    "MODEL_CALL_START",
    "MODEL_CALL_END",
    "RUN_CANCELLED",
}


def _extract_sse_event_type(event: str) -> str:
    """从 SSE 文本中提取事件类型。"""
    for line in event.splitlines():
        if line.startswith("event:"):
            return line.removeprefix("event:").strip()
    return ""


def _status_from_runtime_event_chunk(event_type: str, raw_event: str) -> str:
    """Runtime Event 优先决定后台 Run 状态。"""

    if event_type != SSE_EVENT_RUNTIME:
        return ""
    _, data = _parse_sse_event(raw_event)
    runtime_type = str(data.get("type") or "")
    if runtime_type in RUNTIME_TERMINAL_STATUS:
        return RUNTIME_TERMINAL_STATUS[runtime_type]
    if runtime_type == "RUN_WAITING":
        payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
        wait_state = str(payload.get("waitState") or data.get("status") or "")
        return wait_state if is_waiting_run_status(wait_state) else "waiting_user"
    if runtime_type in RUNTIME_RUNNING_TYPES:
        return "running"
    return ""


def _base_progress_for_status(status: str) -> int:
    if status == "completed":
        return 100
    if status in {"failed", "cancelled"}:
        return 100
    if is_waiting_run_status(status):
        return 65
    if status == "cancelling":
        return 90
    return 8


def _stage_for_status(status: str) -> str:
    return stage_for_run_status(status)


def _parse_sse_event(raw_event: str) -> tuple[str, dict]:
    event_type = ""
    data_text = "{}"
    for line in raw_event.splitlines():
        if line.startswith("event:"):
            event_type = line.removeprefix("event:").strip()
        elif line.startswith("data:"):
            data_text = line.removeprefix("data:").strip()
    try:
        data = json.loads(data_text)
    except json.JSONDecodeError:
        data = {}
    return event_type, data if isinstance(data, dict) else {}


def _run_state_sse(run: Any) -> str:
    """把当前 RunState 包装为稳定 SSE 事件。"""

    state = run.state or initial_run_state(run)
    return _sse_event("RUN_STATE", dump_run_state(normalize_run_state(state)))


def _run_state_patch_sse(
    run: Any,
    event_type: str,
    data: dict,
    *,
    display_event: dict | None = None,
) -> str:
    """把单次事件转换为轻量 RunState patch。"""

    state = run.state or initial_run_state(run)
    patch: dict[str, object] = {
        "kind": "display_event" if display_event else _run_state_patch_kind(event_type, data),
        "runId": run.id,
        "sessionId": run.session_id,
        "status": state.get("status") or run.status,
        "currentStage": state.get("currentStage") or "",
        "updatedAt": state.get("updatedAt") or "",
        "completedAt": state.get("completedAt"),
        "lastEventType": event_type,
        "eventCount": state.get("eventCount") or run.event_count,
    }
    if display_event:
        patch["displayEvent"] = display_event
    if event_type in {"MODEL_CALL_START", "MODEL_CALL_END"}:
        patch["modelName"] = state.get("modelName") or ""
        patch["tokenUsage"] = state.get("tokenUsage")
    elif event_type == SSE_EVENT_RUNTIME:
        runtime_type = str(data.get("type") or "")
        payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
        if runtime_type == "HITL_RESOLVED":
            interaction_id = str(payload.get("interactionId") or data.get("interactionId") or "")
            if interaction_id:
                patch["resolvedApprovalId"] = interaction_id
        elif runtime_type == "MEMORY_SKILL_GENERATING":
            patch["notice"] = {
                "type": runtime_type,
                "message": str(payload.get("message") or "Skills 生成中"),
            }
    elif event_type == SSE_EVENT_TEAM:
        patch["teamEvent"] = data
    elif event_type == "ERROR":
        patch["error"] = str(data.get("message") or state.get("error") or "执行失败")
    return _sse_event("RUN_STATE_PATCH", dump_run_state_patch(patch))


def _should_emit_run_state_metadata_patch(event_type: str, data: dict) -> bool:
    """判断没有展示事件时，是否仍需通知前端状态变化。"""

    if event_type in RUN_STATE_METADATA_PATCH_EVENTS:
        return True
    if event_type == SSE_EVENT_RUNTIME:
        runtime_type = str(data.get("type") or "")
        return runtime_type in {"RUN_WAITING", "HITL_RESOLVED", "MEMORY_SKILL_GENERATING"}
    if event_type == SSE_EVENT_TEAM:
        return True
    return False


def _run_state_patch_kind(event_type: str, data: dict) -> str:
    """把原始事件归类为前端可稳定分支的 patch kind。"""

    if event_type in {
        "REASONING_DELTA",
        "TEXT_BLOCK_DELTA",
        "FINAL_ANSWER_DELTA",
        "FINAL_ANSWER_REPLACE",
        "THINKING_BLOCK_DELTA",
        "TOOL_CALL_START",
        "TOOL_CALL_DELTA",
        "TOOL_CALL_END",
        "TOOL_RESULT_TEXT_DELTA",
        "TOOL_RESULT_END",
        "SKILL_CONTEXT",
        "SKILL_END",
        "HITL_REQUIRED",
        "CLARIFICATION_REQUIRED",
    }:
        return "display_event"
    if event_type in {"MODEL_CALL_START", "MODEL_CALL_END"}:
        return "model_usage"
    if event_type == SSE_EVENT_RUNTIME:
        runtime_type = str(data.get("type") or "")
        if runtime_type == "HITL_RESOLVED":
            return "approval_resolved"
        if runtime_type == "MEMORY_SKILL_GENERATING":
            return "notice"
        if runtime_type.startswith("PLAN_"):
            return "display_event"
        return "snapshot_meta"
    if event_type == SSE_EVENT_TEAM:
        return "team_event"
    if event_type == "ERROR":
        return "display_event"
    return "snapshot_meta"
