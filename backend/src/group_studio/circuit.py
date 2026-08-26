"""轮次与无进展熔断。cancelled 因插话/暂停不计 no_progress。"""

from __future__ import annotations

from typing import Any

from src.group_studio.events import emit_envelope
from src.group_studio.notify import KIND_CIRCUIT, notify_studio
from src.group_studio.sessions import take_fingerprint
from src.storage.database import get_group_studio_snapshot, update_group_studio_state

BILLABLE_STATUSES = frozenset({"discussing", "executing", "reviewing"})


def progress_fingerprint(snapshot: dict[str, Any]) -> tuple[Any, ...]:
    """黑板 revision / 产物 / 任务卡 revision / DoD 勾选。"""

    board = snapshot.get("blackboard") or {}
    card = snapshot.get("taskCard") or {}
    dod = card.get("dodChecklist") or []
    checked = tuple(
        str(item.get("id") or "")
        for item in dod
        if isinstance(item, dict) and item.get("checked")
    )
    artifacts = tuple(str(item) for item in (board.get("artifacts") or []))
    return (
        int(board.get("revision") or 0),
        artifacts,
        int(card.get("revision") or 0),
        checked,
    )


def on_run_finished(
    group_id: str,
    *,
    run_id: str,
    run_status: str,
    interrupt_source: str | None = None,
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
        if started is not None and started == progress_fingerprint(snapshot):
            budget["noProgressTurns"] = int(budget.get("noProgressTurns") or 0) + 1
        else:
            budget["noProgressTurns"] = 0
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
        status="circuit_broken",
        phase_before_pause=status,
        budget=budget,
    )
    emit_envelope(
        group_id=group_id,
        event_type="GROUP_STATUS",
        payload={"from": status, "to": "circuit_broken", "reason": reason},
    )
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
