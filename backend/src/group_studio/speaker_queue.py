"""发言队列：只认 Worker，不解析正文 @。"""
from __future__ import annotations


from typing import Any

from src.group_studio.events import emit_envelope
from src.storage.records.group_studio_records import (
    get_group_studio_snapshot,
    get_group_studio_speaker_queue,
    get_group_studio_wave_spoken,
    pop_group_studio_speaker_queue_head,
    set_group_studio_speaker_queue,
    set_group_studio_wave_spoken,
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
    """清空点名队列。interrupt 用；不发 drained。"""

    set_queue(group_id, [], kind=kind, run_id=run_id)


def drop_member(group_id: str, member_id: str, *, kind: str = "interrupt") -> list[str]:
    """从队列拿掉一个人。"""

    remaining = [item for item in get_queue(group_id) if item != member_id]
    return set_queue(group_id, remaining, kind=kind)


def is_runnable_worker(member: dict[str, Any] | None) -> bool:
    """未静音、未移除、未崩溃的 Worker。"""

    if member is None:
        return False
    if str(member.get("role") or "worker") == "host":
        return False
    if member.get("status") in {"removed", "crashed"}:
        return False
    if member.get("muted"):
        return False
    return True


def runnable_workers(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """按 joinedAt、id 排序的可跑 Worker。"""

    members = [
        item
        for item in (snapshot.get("members") or [])
        if is_runnable_worker(item)
    ]
    return sorted(
        members,
        key=lambda item: (str(item.get("joinedAt") or ""), str(item.get("id") or "")),
    )


def build_turn_queue(snapshot: dict[str, Any], mentions: list[str] | None) -> tuple[list[str], str]:
    """从人类 mentions 算本轮队列。

    返回 (member_ids, kind)。kind 为 assign / all_round / empty。
    mentions 非空但一个都不能跑 → ([], "assign")，调用方发 no_eligible_speaker。
    mentions 为空且没人可跑 → ([], "all_round")。
    """

    by_id = {
        str(item.get("id") or ""): item
        for item in (snapshot.get("members") or [])
    }
    cleaned = [str(item or "").strip() for item in (mentions or []) if str(item or "").strip()]
    if cleaned:
        seen: list[str] = []
        for member_id in cleaned:
            if member_id in seen:
                continue
            if is_runnable_worker(by_id.get(member_id)):
                seen.append(member_id)
        return seen, "assign"
    return [str(item.get("id")) for item in runnable_workers(snapshot)], "all_round"


def pop_unrunnable_head(group_id: str) -> None:
    """弹出 muted / removed / crashed / 缺失的队首。"""

    snapshot = get_group_studio_snapshot(group_id, message_limit=1) or {}
    members = {
        str(item.get("id") or ""): item
        for item in (snapshot.get("members") or [])
    }
    queue = get_group_studio_speaker_queue(group_id)
    remaining = [item for item in queue if is_runnable_worker(members.get(item))]
    if remaining != queue:
        set_group_studio_speaker_queue(group_id, remaining)


def get_wave_spoken(group_id: str) -> list[str]:
    """本波已完成回合的工人。"""

    return get_group_studio_wave_spoken(group_id)


def reset_wave_spoken(group_id: str) -> None:
    """人类新消息 / 停止后清空本波已发言。"""

    set_group_studio_wave_spoken(group_id, [])


def note_wave_spoken(group_id: str, member_id: str) -> list[str]:
    """工人回合终态记入本波。"""

    spoken = get_wave_spoken(group_id)
    cleaned = str(member_id or "").strip()
    if cleaned and cleaned not in spoken:
        spoken = [*spoken, cleaned]
        set_group_studio_wave_spoken(group_id, spoken)
    return spoken


def enqueue_handoff(group_id: str, from_id: str, next_id: str) -> bool:
    """把有效提名追加到剩余队列末尾。无效返回 False，不改队列。"""

    source = str(from_id or "").strip()
    target = str(next_id or "").strip()
    if not target or target == source:
        return False
    snapshot = get_group_studio_snapshot(group_id) or {}
    members = {
        str(item.get("id") or ""): item
        for item in (snapshot.get("members") or [])
    }
    if not is_runnable_worker(members.get(target)):
        return False
    if target in get_wave_spoken(group_id):
        return False
    remaining = get_queue(group_id)
    if target in remaining:
        return False
    set_queue(group_id, [*remaining, target], kind="handoff")
    return True


def pop_head(group_id: str, member_id: str) -> str:
    """工人 Run 开始时弹出自己，并广播剩余队列。不匹配则空串。"""

    popped = pop_group_studio_speaker_queue_head(group_id, member_id)
    if popped:
        remaining = get_group_studio_speaker_queue(group_id)
        emit_envelope(
            group_id=group_id,
            event_type="GROUP_SPEAKER_QUEUE",
            payload={
                "memberIds": remaining,
                "kind": "queue_next",
                "pendingMemberId": remaining[0] if remaining else None,
            },
        )
    return popped
