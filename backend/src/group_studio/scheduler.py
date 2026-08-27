"""发言权调度：进入 discussing 后给 Host 一轮。pytest 下默认不自动点火以免打到真实模型。"""

from __future__ import annotations

import asyncio
import os

from src.group_studio.host_runtime import run_host_discussion
from src.group_studio.worker_runtime import run_worker_turn
from src.storage.database import get_group_studio_snapshot


def should_auto_start_host() -> bool:
    """单测默认关闭自动 Host，避免 from-chat 测试触发模型。"""

    return not os.environ.get("PYTEST_CURRENT_TEST")


def schedule_host_turn(group_id: str) -> None:
    """若工作室可发言且没有进行中的 Run，则后台启动 Host 一轮。

    已落库但尚未开工的指派优先拉工人，避免 Host 缓存 miss 后房间空转。
    """

    if not should_auto_start_host():
        return
    snapshot = get_group_studio_snapshot(group_id, message_limit=1)
    if snapshot is None:
        return
    status = str(snapshot.get("status") or "")
    goal = str((snapshot.get("taskCard") or {}).get("goal") or "").strip()
    if status not in {"discussing", "executing", "reviewing", "awaiting_human"} or not goal:
        return
    if snapshot.get("currentRunId"):
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    pending = str(snapshot.get("pendingSpeakerMemberId") or "").strip()
    if pending and status != "awaiting_human":
        loop.create_task(_run_worker_guarded(group_id, pending))
        return
    loop.create_task(_run_host_guarded(group_id))


def schedule_worker_turn(group_id: str, member_id: str) -> None:
    """Host 指派后启动工人一轮。pytest 下不自动点火。"""

    if not should_auto_start_host():
        return
    snapshot = get_group_studio_snapshot(group_id, message_limit=1)
    if snapshot is None or snapshot.get("currentRunId"):
        return
    if str(snapshot.get("status") or "") == "awaiting_human":
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(_run_worker_guarded(group_id, member_id))


async def _run_host_guarded(group_id: str, *, phase_followup: bool = False) -> None:
    try:
        before = get_group_studio_snapshot(group_id, message_limit=1) or {}
        before_status = str(before.get("status") or "")
        assigned = await run_host_discussion(group_id)
        if assigned:
            await _run_worker_guarded(group_id, assigned)
            return
        after = get_group_studio_snapshot(group_id, message_limit=1) or {}
        after_status = str(after.get("status") or "")
        if (
            not phase_followup
            and after_status in {"executing", "reviewing"}
            and after_status != before_status
        ):
            await _run_host_guarded(group_id, phase_followup=True)
    except Exception:
        from src.group_studio.events import emit_envelope

        emit_envelope(
            group_id=group_id,
            event_type="GROUP_ERROR",
            payload={"code": "host_failed", "message": "host turn crashed"},
        )


async def _run_worker_guarded(group_id: str, member_id: str) -> None:
    try:
        await run_worker_turn(group_id, member_id)
        schedule_host_turn(group_id)
    except Exception:
        from src.group_studio.events import emit_envelope

        emit_envelope(
            group_id=group_id,
            event_type="GROUP_ERROR",
            payload={"code": "worker_failed", "message": "worker turn crashed"},
        )
