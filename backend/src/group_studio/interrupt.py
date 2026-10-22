"""取消当前 GroupRun：插话、暂停、停止。不调用 Chat background cancel。"""
from __future__ import annotations


from src.group_studio.events import emit_envelope
from src.group_studio.sessions import (
    cancel_run_task,
    cancel_sessions_for_run,
    mark_interrupt,
)
from src.storage.records.group_studio_records import (
    get_group_studio_pending_approval,
    get_group_studio_snapshot,
    set_group_studio_current_run_id,
    update_group_studio_approval_status,
    update_group_studio_run_status,
)


def cancel_active_run(group_id: str, *, source: str) -> bool:
    """取消当前 Run 与 pending 审批。没有活动 Run 时返回 False。"""

    snapshot = get_group_studio_snapshot(group_id, message_limit=1)
    if snapshot is None:
        return False
    run_id = snapshot.get("currentRunId")
    if not run_id:
        return False
    mark_interrupt(group_id, source)
    pending = get_group_studio_pending_approval(group_id, str(run_id))
    if pending is not None:
        updated = update_group_studio_approval_status(str(pending["id"]), "cancelled") or pending
        updated["status"] = "cancelled"
        emit_envelope(
            group_id=group_id,
            event_type="GROUP_APPROVAL",
            run_id=str(run_id),
            payload=updated,
        )
    cancel_sessions_for_run(group_id, str(run_id))
    cancel_run_task(group_id)
    update_group_studio_run_status(str(run_id), "cancelled", source)
    set_group_studio_current_run_id(group_id, None)
    emit_envelope(
        group_id=group_id,
        event_type="GROUP_INTERRUPT",
        run_id=str(run_id),
        payload={"runId": run_id, "source": source},
    )
    emit_envelope(
        group_id=group_id,
        event_type="GROUP_RUN_ENDED",
        run_id=str(run_id),
        payload={"runId": run_id, "status": "cancelled"},
    )
    return True
