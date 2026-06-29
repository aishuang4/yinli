"""应用级 Runtime Event 包装层。"""

import uuid
from datetime import datetime, timezone
from typing import Any

from src.server.routes.chat.background_runs import current_chat_background_run_id
from src.server.routes.chat.event_types import (
    AGENTSCOPE_TO_RUNTIME_EVENT,
    SSE_EVENT_RUNTIME,
)
from src.server.routes.chat.helpers import _sse_event


def build_runtime_event(
    event_type: str,
    *,
    session_id: str,
    scope: str,
    run_id: str | None = None,
    source: str = "runtime",
    source_event_type: str | None = None,
    agent_id: str | None = None,
    agent_name: str | None = None,
    team_activity_id: str | None = None,
    status: str | None = None,
    reason: str | None = None,
    duration_ms: int | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造统一 Runtime Event payload。"""

    return {
        "id": uuid.uuid4().hex,
        "runId": run_id if run_id is not None else current_chat_background_run_id(),
        "sessionId": session_id,
        "type": event_type,
        "source": source,
        "sourceEventType": source_event_type,
        "scope": scope,
        "agentId": agent_id or "",
        "agentName": agent_name or "",
        "teamActivityId": team_activity_id,
        "status": status or "",
        "reason": reason or "",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "durationMs": duration_ms,
        "payload": payload or {},
    }


def runtime_event_sse(event_type: str, collect: list[dict[str, Any]] | None = None, **kwargs: Any) -> str:
    """把 Runtime Event 包装为 SSE RUNTIME_EVENT。"""

    event = build_runtime_event(event_type, **kwargs)
    _record_runtime_event_trace(event)
    if collect is not None:
        _append_collected_runtime_event(collect, event)
    return _sse_event(SSE_EVENT_RUNTIME, event)


def runtime_event_from_agentscope_sse(
    source_event_type: str,
    *,
    session_id: str,
    scope: str,
    agent_id: str,
    agent_name: str,
    team_activity_id: str | None = None,
    event_data: dict[str, Any] | None = None,
    collect: list[dict[str, Any]] | None = None,
) -> str | None:
    """把 AgentScope 原始事件映射为应用级 Runtime Event。"""

    runtime_type = AGENTSCOPE_TO_RUNTIME_EVENT.get(source_event_type)
    if not runtime_type:
        return None
    return runtime_event_sse(
        runtime_type,
        session_id=session_id,
        scope=scope,
        source="agentscope",
        source_event_type=source_event_type,
        agent_id=agent_id,
        agent_name=agent_name,
        team_activity_id=team_activity_id,
        status=_status_for_runtime_event(runtime_type, event_data or {}),
        payload=_compact_agentscope_payload(source_event_type, event_data or {}),
        collect=collect,
    )


def _status_for_runtime_event(event_type: str, event_data: dict[str, Any] | None = None) -> str:
    if event_type == "TOOL_RESULT_COMPLETED":
        state = str((event_data or {}).get("state") or "").lower()
        if state in {"error", "failed", "denied"}:
            return "failed"
    if event_type.endswith("_COMPLETED") or event_type in {"AGENT_COMPLETED", "TEAM_COMPLETED", "RUN_COMPLETED"}:
        return "completed"
    if event_type.endswith("_FAILED") or event_type in {"AGENT_FAILED", "TEAM_FAILED", "RUN_FAILED"}:
        return "failed"
    if "WAITING" in event_type:
        return "waiting"
    return "running"


def _record_runtime_event_trace(event: dict[str, Any]) -> None:
    """把 Runtime Event 轻量写入运行轨迹，供 Trace 页面按业务事件筛选。"""

    session_id = str(event.get("sessionId") or "")
    if not session_id:
        return
    if str(event.get("type") or "") in {
        "AGENT_OUTPUT_DELTA",
        "AGENT_REASONING_DELTA",
        "AGENT_FINAL_ANSWER_DELTA",
        "AGENT_THINKING",
        "TOOL_CALL_DELTA",
        "TOOL_RESULT_DELTA",
    }:
        return
    try:
        from src.server.routes.chat.trace_recorder import _record_run_event

        _record_run_event(
            session_id=session_id,
            event_type=SSE_EVENT_RUNTIME,
            persist=True,
            scope=str(event.get("scope") or "run"),
            agent_id=str(event.get("agentId") or "") or None,
            agent_name=str(event.get("agentName") or "") or None,
            team_activity_id=event.get("teamActivityId"),
            status=str(event.get("status") or ""),
            title=str(event.get("type") or SSE_EVENT_RUNTIME),
            content=str(event.get("reason") or ""),
            payload=event,
        )
    except Exception as exc:
        print(f"[RuntimeEvent] Failed to record trace event: {exc}")


def _append_collected_runtime_event(events: list[dict[str, Any]], event: dict[str, Any]) -> None:
    """压缩持久化用 Runtime Event，避免 token 级 delta 挤掉前面的 ReAct 步骤。"""

    if events and _should_merge_runtime_delta(events[-1], event):
        previous = events[-1]
        previous_payload = dict(previous.get("payload") or {})
        event_payload = dict(event.get("payload") or {})
        previous_payload["delta"] = f"{previous_payload.get('delta') or ''}{event_payload.get('delta') or ''}"
        previous["timestamp"] = event.get("timestamp") or previous.get("timestamp")
        previous["payload"] = previous_payload
        return
    events.append(event)


def _should_merge_runtime_delta(previous: dict[str, Any], event: dict[str, Any]) -> bool:
    if previous.get("type") != event.get("type"):
        return False
    if previous.get("scope") != event.get("scope"):
        return False
    if previous.get("agentId") != event.get("agentId"):
        return False
    if previous.get("teamActivityId") != event.get("teamActivityId"):
        return False
    if event.get("type") not in {
        "AGENT_OUTPUT_DELTA",
        "AGENT_REASONING_DELTA",
        "AGENT_FINAL_ANSWER_DELTA",
        "AGENT_THINKING",
        "TOOL_CALL_DELTA",
        "TOOL_RESULT_DELTA",
    }:
        return False
    previous_payload = dict(previous.get("payload") or {})
    event_payload = dict(event.get("payload") or {})
    return str(previous_payload.get("tool_call_id") or "") == str(event_payload.get("tool_call_id") or "")


def _compact_agentscope_payload(source_event_type: str, event_data: dict[str, Any]) -> dict[str, Any]:
    """保留 UI 需要的轻量字段，避免 Runtime Event 过大。"""

    keys = [
        "model_name",
        "tool_call_id",
        "tool_call_name",
        "reply_id",
        "delta",
        "state",
        "input_tokens",
        "output_tokens",
    ]
    payload = {key: event_data[key] for key in keys if key in event_data}
    payload["sourceEventType"] = source_event_type
    return payload
