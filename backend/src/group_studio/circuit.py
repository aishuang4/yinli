"""轮次与无进展熔断。cancelled 因插话/暂停不计 no_progress。"""

from __future__ import annotations

from typing import Any

from src.group_studio.events import emit_envelope
from src.group_studio.notify import KIND_CIRCUIT, notify_studio
from src.group_studio.sessions import take_fingerprint
from src.storage.database import (
    get_group_studio_snapshot,
    list_group_studio_messages,
    update_group_studio_state,
)

BILLABLE_STATUSES = frozenset({"open"})


def _worker_delivered(group_id: str, run_id: str) -> bool:
    """工人本轮写出了可见正文，算有效进展。"""

    for item in list_group_studio_messages(group_id, limit=30):
        if str(item.get("runId") or "") != run_id:
            continue
        if str(item.get("role") or "") != "worker":
            continue
        if str(item.get("body") or "").strip():
            return True
    return False


def progress_fingerprint(snapshot: dict[str, Any]) -> tuple[Any, ...]:
    """无进展指纹：置顶修订、可见工人正文。"""

    pin = snapshot.get("pin") or {}
    card = snapshot.get("taskCard") or {}
    messages = snapshot.get("messages") or []
    worker_bodies = tuple(
        str(item.get("body") or "")
        for item in messages
        if str(item.get("role") or "") == "worker" and str(item.get("body") or "").strip()
    )
    return (
        str(pin.get("goal") or ""),
        str(pin.get("notes") or ""),
        int(card.get("revision") or 0),
        worker_bodies[-3:],
    )


def on_run_finished(
    group_id: str,
    *,
    run_id: str,
    run_status: str,
    interrupt_source: str | None = None,
    duty: str | None = None,
) -> bool:
    """计费 Run 结束后更新预算；触顶则熔断。返回是否已熔断。"""

    snapshot = get_group_studio_snapshot(group_id, message_limit=1)
    if snapshot is None:
        return False
    status = str(snapshot.get("status") or "")
    if status not in BILLABLE_STATUSES:
        take_fingerprint(run_id)
        return False
    budget = dict(snapshot.get("budget") or {
        "maxTurns": 40,
        "maxNoProgressTurns": 3,
        "usedTurns": 0,
        "noProgressTurns": 0,
    })
    human_cancel = run_status == "cancelled" and interrupt_source in {
        "human_message",
        "stop_button",
        "task_card",
        "pause",
    }
    started = take_fingerprint(run_id)
    if run_status == "completed":
        budget["usedTurns"] = int(budget.get("usedTurns") or 0) + 1
        board_moved = started is None or started != progress_fingerprint(snapshot)
        if board_moved or _worker_delivered(group_id, run_id):
            budget["noProgressTurns"] = 0
        else:
            budget["noProgressTurns"] = int(budget.get("noProgressTurns") or 0) + 1
    elif human_cancel:
        pass
    else:
        budget["noProgressTurns"] = int(budget.get("noProgressTurns") or 0) + 1

    emit_envelope(
        group_id=group_id,
        event_type="GROUP_BUDGET",
        payload=budget,
    )
    reason = ""
    if int(budget.get("usedTurns") or 0) >= int(budget.get("maxTurns") or 40):
        reason = "max_turns"
    elif int(budget.get("noProgressTurns") or 0) >= int(budget.get("maxNoProgressTurns") or 3):
        reason = "no_progress"
    update_group_studio_state(group_id, budget=budget)
    if not reason:
        return False
    update_group_studio_state(
        group_id,
        status="paused",
        phase_before_pause="circuit",
        budget=budget,
    )
    emit_envelope(
        group_id=group_id,
        event_type="GROUP_STATUS",
        payload={"from": status, "to": "paused", "reason": "circuit"},
    )
    from src.group_studio.service import _emit_system_notice

    _emit_system_notice(group_id, "先停一下：这一轮说得有点多。点继续或再发一条就可以接着聊。")
    emit_envelope(
        group_id=group_id,
        event_type="GROUP_CIRCUIT",
        payload={
            "reason": reason,
            "usedTurns": budget.get("usedTurns") or 0,
            "noProgressTurns": budget.get("noProgressTurns") or 0,
        },
    )
    latest = get_group_studio_snapshot(group_id, message_limit=1) or snapshot
    notify_studio(
        latest,
        kind=KIND_CIRCUIT,
        body="轮次或无进展已达上限，工作室已暂停。",
    )
    return True
