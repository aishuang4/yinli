"""工作室生命周期收口：启动对账、删除 Chat session 归档。"""

from __future__ import annotations

from src.group_studio.events import emit_envelope
from src.group_studio.interrupt import cancel_active_run
from src.group_studio.notify import KIND_CIRCUIT, notify_studio
from src.storage.database import (
    archive_group_studio,
    get_active_group_studio_by_chat_session,
    get_group_studio_snapshot,
    list_group_studios_with_current_run,
    update_group_studio_state,
)


def archive_for_chat_session_delete(session_id: str) -> None:
    """无论 flag，删除 Chat session 时先取消 Run 再归档。"""

    raw = get_active_group_studio_by_chat_session(session_id)
    if raw is None:
        return
    group_id = str(raw["id"])
    previous = str(raw.get("status") or "")
    cancel_active_run(group_id, source="pause")
    archive_group_studio(group_id)
    emit_envelope(
        group_id=group_id,
        event_type="GROUP_STATUS",
        payload={"from": previous, "to": "archived", "reason": "chat_session_deleted"},
    )


def reconcile_interrupted_studios() -> int:
    """进程启动：有未收口 Run 的工作室按 pause 收口，并通知已暂停。"""

    rows = list_group_studios_with_current_run()
    for row in rows:
        group_id = str(row["id"])
        previous = str(row.get("status") or "")
        cancel_active_run(group_id, source="pause")
        if previous != "paused":
            update_group_studio_state(
                group_id,
                status="paused",
                phase_before_pause=previous,
            )
            emit_envelope(
                group_id=group_id,
                event_type="GROUP_STATUS",
                payload={"from": previous, "to": "paused", "reason": "startup_reconcile"},
            )
        snapshot = get_group_studio_snapshot(group_id, message_limit=1) or row
        notify_studio(
            snapshot,
            kind=KIND_CIRCUIT,
            title="工作室已暂停",
            body="服务重启，工作室已暂停。",
        )
    return len(rows)
