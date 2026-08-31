"""点名 FIFO 队列：写库、弹不可运行队首、发 GROUP_SPEAKER_QUEUE。"""

from __future__ import annotations

from typing import Any

from src.group_studio.events import emit_envelope
from src.storage.database import (
    get_group_studio_snapshot,
    get_group_studio_speaker_queue,
    pop_group_studio_speaker_queue_head,
    set_group_studio_speaker_queue,
)


def get_queue(group_id: str) -> list[str]:
    """当前剩余点名队列。"""

    return get_group_studio_speaker_queue(group_id)


def set_queue(group_id: str, member_ids: list[str], *, kind: str, run_id: str | None = None) -> list[str]:
    """写入队列并发 SSE。"""

    set_group_studio_speaker_queue(group_id, member_ids)
    remaining = get_group_studio_speaker_queue(group_id)
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


def clear_queue(group_id: str, *, kind: str, run_id: str | None = None) -> None:
    """清空点名队列与认领轮。interrupt / restatement 用；不发 drained。"""

    from src.group_studio.workboard import clear_claim_round

    set_queue(group_id, [], kind=kind, run_id=run_id)
    clear_claim_round(group_id)


def pop_unrunnable_head(group_id: str) -> None:
    """弹出 muted / removed / crashed / 缺失的队首，直到队首可运行或队列空。"""

    snapshot = get_group_studio_snapshot(group_id, message_limit=1) or {}
    members = {
        str(item.get("id") or ""): item
        for item in (snapshot.get("members") or [])
    }
    queue = get_group_studio_speaker_queue(group_id)
    _rewrite_runnable_prefix(group_id, queue, members)


def _rewrite_runnable_prefix(
    group_id: str,
    queue: list[str],
    members: dict[str, Any],
) -> None:
    remaining = list(queue)
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
    if remaining != queue:
        set_group_studio_speaker_queue(group_id, remaining)


def pop_head(group_id: str, member_id: str) -> str:
    """工人 Run 开始时弹出自己。不匹配则空串。"""

    return pop_group_studio_speaker_queue_head(group_id, member_id)
