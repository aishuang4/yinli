"""工作项发布、原子认领、认领轮与 Host 回合末调度决策。"""

from __future__ import annotations

import uuid
from typing import Any

from src.group_studio.errors import (
    INTAKE_NOT_CONFIRMED,
    WORK_ITEM_CLAIMED,
    WORK_ITEM_NOT_OWNED,
    GroupStudioError,
)
from src.group_studio.events import emit_envelope
from src.group_studio.speaker_queue import get_queue, set_queue
from src.storage.database import (
    cancel_group_studio_work_item,
    claim_group_studio_work_item,
    complete_group_studio_work_item,
    get_group_studio_claim_round,
    get_group_studio_member,
    get_group_studio_snapshot,
    insert_group_studio_work_item,
    list_group_studio_work_items,
    pop_group_studio_claim_round_head,
    review_group_studio_work_item,
    set_group_studio_claim_round,
)


def _new_item_id() -> str:
    return f"gwitem_{uuid.uuid4().hex[:16]}"


def get_claim_round(group_id: str) -> list[str]:
    return get_group_studio_claim_round(group_id)


def set_claim_round(group_id: str, member_ids: list[str], *, kind: str, run_id: str | None = None) -> list[str]:
    set_group_studio_claim_round(group_id, member_ids)
    remaining = get_group_studio_claim_round(group_id)
    emit_envelope(
        group_id=group_id,
        event_type="GROUP_SPEAKER_QUEUE",
        run_id=run_id,
        payload={
            "memberIds": remaining,
            "kind": kind,
            "pendingMemberId": remaining[0] if remaining else None,
        },
    )
    return remaining


def clear_claim_round(group_id: str) -> None:
    set_group_studio_claim_round(group_id, [])


def pop_claim_head(group_id: str, member_id: str) -> str:
    return pop_group_studio_claim_round_head(group_id, member_id)


def pop_unrunnable_claim(group_id: str) -> None:
    snapshot = get_group_studio_snapshot(group_id, message_limit=1) or {}
    members = {str(item.get("id") or ""): item for item in (snapshot.get("members") or [])}
    remaining = get_group_studio_claim_round(group_id)
    original = list(remaining)
    while remaining:
        member = members.get(remaining[0])
        if (
            member is None
            or member.get("role") != "worker"
            or member.get("status") in {"removed", "crashed"}
            or member.get("muted")
        ):
            remaining = remaining[1:]
            continue
        break
    if remaining != original:
        set_group_studio_claim_round(group_id, remaining)


def unmuted_workers(snapshot: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for item in snapshot.get("members") or []:
        if item.get("role") != "worker" or item.get("status") == "removed":
            continue
        if item.get("muted"):
            continue
        ids.append(str(item.get("id") or ""))
    return [item for item in ids if item]


def publish_work_items(
    group_id: str,
    items: list[dict[str, Any]],
    *,
    run_id: str,
    assigned_member_ids: list[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    """插入工作项；带 assignee 的立刻原子认领并入队。"""

    snapshot = get_group_studio_snapshot(group_id) or {}
    if str(snapshot.get("status") or "") == "drafting":
        raise GroupStudioError(409, INTAKE_NOT_CONFIRMED)
    card = snapshot.get("taskCard") or {}
    created: list[dict[str, Any]] = []
    enqueue = list(assigned_member_ids)
    existing = list_group_studio_work_items(group_id)
    sort_base = len(existing)
    for index, raw in enumerate(items):
        title = str(raw.get("title") or "").strip()
        if not title:
            continue
        row = insert_group_studio_work_item({
            "id": _new_item_id(),
            "groupId": group_id,
            "taskCardId": str(card.get("id") or snapshot.get("currentTaskCardId") or ""),
            "title": title,
            "description": str(raw.get("description") or ""),
            "status": "open",
            "sortOrder": sort_base + index,
            "sourceRunId": run_id,
        })
        assignee = str(raw.get("assigneeMemberId") or "").strip()
        if assignee:
            claimed = claim_group_studio_work_item(
                group_id, row["id"], member_id=assignee, run_id=run_id,
            )
            if claimed is not None:
                row = claimed
                if assignee not in enqueue:
                    enqueue.append(assignee)
        created.append(row)
    emit_envelope(
        group_id=group_id,
        event_type="GROUP_WORK_ITEM",
        run_id=run_id,
        payload={"change": "created", "items": created},
    )
    if enqueue:
        set_queue(group_id, enqueue, kind="assign", run_id=run_id)
    return created, enqueue


def claim_item(group_id: str, work_item_id: str, *, member_id: str, run_id: str) -> dict[str, Any]:
    member = get_group_studio_member(group_id, member_id)
    if member is None or member.get("muted"):
        raise GroupStudioError(409, "member_muted")
    row = claim_group_studio_work_item(
        group_id, work_item_id, member_id=member_id, run_id=run_id,
    )
    if row is None:
        raise GroupStudioError(409, WORK_ITEM_CLAIMED)
    emit_envelope(
        group_id=group_id,
        event_type="GROUP_WORK_ITEM",
        run_id=run_id,
        payload={"change": "claimed", "items": [row]},
    )
    return row


def complete_item(group_id: str, work_item_id: str, *, member_id: str, run_id: str) -> dict[str, Any]:
    row = complete_group_studio_work_item(group_id, work_item_id, member_id=member_id)
    if row is None:
        raise GroupStudioError(409, WORK_ITEM_NOT_OWNED)
    emit_envelope(
        group_id=group_id,
        event_type="GROUP_WORK_ITEM",
        run_id=run_id,
        payload={"change": "completed", "items": [row]},
    )
    return row


def review_item(
    group_id: str,
    work_item_id: str,
    *,
    verdict: str,
    note: str = "",
    run_id: str = "",
) -> dict[str, Any]:
    """验收期对 done 工作项打 accept / rework；rework 退回 open 并把意见带给原认领人。"""

    row = review_group_studio_work_item(group_id, work_item_id, verdict=verdict, note=note)
    if row is None:
        raise GroupStudioError(409, WORK_ITEM_NOT_OWNED)
    emit_envelope(
        group_id=group_id,
        event_type="GROUP_WORK_ITEM",
        run_id=run_id or None,
        payload={"change": "accepted" if verdict == "accept" else "rework", "items": [row]},
    )
    return row


def cancel_item(group_id: str, work_item_id: str, *, run_id: str = "") -> dict[str, Any]:
    snapshot = get_group_studio_snapshot(group_id, message_limit=1) or {}
    if str(snapshot.get("status") or "") == "drafting":
        raise GroupStudioError(409, INTAKE_NOT_CONFIRMED)
    row = cancel_group_studio_work_item(group_id, work_item_id)
    if row is None:
        raise GroupStudioError(409, WORK_ITEM_NOT_OWNED)
    emit_envelope(
        group_id=group_id,
        event_type="GROUP_WORK_ITEM",
        run_id=run_id or None,
        payload={"change": "cancelled", "items": [row]},
    )
    return row


def finalize_host_dispatch(ctx: Any, group_id: str) -> None:
    """Host 回合结束：队列非空则清认领轮；否则有未指派 open 项则开认领轮。"""

    assigned = list(getattr(ctx, "assigned_member_ids", None) or [])
    queue = get_queue(group_id)
    if assigned or queue:
        clear_claim_round(group_id)
        return
    published = list(getattr(ctx, "published_work_item_ids", None) or [])
    if not published:
        return
    open_unassigned = [
        item for item in list_group_studio_work_items(group_id)
        if item.get("status") == "open" and not item.get("assigneeMemberId")
    ]
    if not open_unassigned:
        return
    snapshot = get_group_studio_snapshot(group_id, message_limit=1) or {}
    workers = unmuted_workers(snapshot)
    if workers:
        set_claim_round(group_id, workers, kind="claim_round", run_id=getattr(ctx, "run_id", None))


def enqueue_claimed_assignees(group_id: str) -> list[str]:
    """认领轮结束后把成功认领者按 claimed_at 去重写入执行队列。"""

    items = [
        item for item in list_group_studio_work_items(group_id)
        if item.get("status") == "claimed" and item.get("assigneeMemberId")
    ]
    items.sort(key=lambda item: str(item.get("claimedAt") or ""))
    ids: list[str] = []
    for item in items:
        member_id = str(item.get("assigneeMemberId") or "")
        if member_id and member_id not in ids:
            ids.append(member_id)
    if ids:
        set_queue(group_id, ids, kind="assign")
    return ids
