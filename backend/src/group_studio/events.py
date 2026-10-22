"""群聊工作室领域事件：持久化非 DELTA，并推送到 SSE 枢纽。"""
from __future__ import annotations


import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

from src.group_studio.stream import publish
from src.storage.records.group_studio_records import (
    append_group_studio_event,
    get_group_studio_snapshot,
)

EPHEMERAL_TYPES = {"GROUP_STUDIO_STATE", "GROUP_MESSAGE_DELTA", "GROUP_ACTIVITY_DELTA"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def emit_envelope(
    *,
    group_id: str,
    event_type: str,
    payload: dict[str, Any] | None = None,
    run_id: str | None = None,
    persist: bool | None = None,
) -> dict[str, Any]:
    """同步构造信封；持久化后尽力推给订阅者。"""

    snapshot = get_group_studio_snapshot(group_id, message_limit=1) or {}
    status = str(snapshot.get("status") or "")
    should_persist = persist if persist is not None else event_type not in EPHEMERAL_TYPES
    created_at = _utc_now()
    if should_persist:
        envelope = append_group_studio_event({
            "id": f"gevt_{uuid.uuid4().hex[:16]}",
            "groupId": group_id,
            "runId": run_id or "",
            "type": event_type,
            "status": status,
            "payload": payload or {},
            "createdAt": created_at,
        })
    else:
        envelope = {
            "seq": None,
            "groupId": group_id,
            "runId": run_id,
            "type": event_type,
            "status": status,
            "payload": payload or {},
            "createdAt": created_at,
        }
    _publish_nowait(group_id, envelope)
    return envelope


def _publish_nowait(group_id: str, envelope: dict[str, Any]) -> None:
    from src.tenancy.context import current_tenant, spawn_in_tenant

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    spawn_in_tenant(current_tenant(), publish(group_id, envelope), name=f"group-event-{group_id}")
