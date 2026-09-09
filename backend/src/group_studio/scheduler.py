"""发言权调度：Host 自然结束默认停；工人结束弹队列下一人。"""

from __future__ import annotations

import asyncio
import logging
import os

from src.group_studio.events import emit_envelope
from src.group_studio.host_runtime import run_host_discussion
from src.group_studio.speaker_queue import get_queue, pop_unrunnable_head
from src.group_studio.workboard import (
    enqueue_claimed_assignees,
    get_claim_round,
    pop_unrunnable_claim,
)
from src.group_studio.worker_runtime import run_worker_turn
from src.storage.database import (
    get_group_studio_snapshot,
    set_group_studio_current_run_id,
    update_group_studio_run_status,
)

logger = logging.getLogger(__name__)


def should_auto_start_host() -> bool:
    """单测默认关闭自动 Host，避免 from-chat 测试触发模型。"""

    return not os.environ.get("PYTEST_CURRENT_TEST")


def schedule_host_turn(group_id: str) -> None:
    """后台启动 Host 一轮。不派工人。"""

    if not should_auto_start_host():
        return
    snapshot = get_group_studio_snapshot(group_id, message_limit=1)
    if snapshot is None:
        return
    status = str(snapshot.get("status") or "")
    goal = str((snapshot.get("taskCard") or {}).get("goal") or "").strip()
    if status not in {"drafting", "discussing", "executing", "reviewing", "awaiting_human"}:
        return
    if status != "drafting" and not goal:
        return
    if snapshot.get("currentRunId"):
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(_run_host_guarded(group_id))


def schedule_next(group_id: str) -> None:
    """只派工人。队列空或 awaiting_human 则 return。"""

    if not should_auto_start_host():
        return
    snapshot = get_group_studio_snapshot(group_id, message_limit=1)
    if snapshot is None or snapshot.get("currentRunId"):
        return
    status = str(snapshot.get("status") or "")
    if status == "awaiting_human":
        return
    if status not in {"discussing", "executing", "reviewing"}:
        return
    pop_unrunnable_head(group_id)
    pop_unrunnable_claim(group_id)
    queue = get_queue(group_id)
    if queue:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(_run_worker_guarded(group_id, queue[0], duty="queue"))
        return
    claim = get_claim_round(group_id)
    if not claim:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(_run_worker_guarded(group_id, claim[0], duty="claim"))


def schedule_worker_turn(group_id: str, member_id: str) -> None:
    """显式启动指定工人一轮。pytest 下不自动点火。"""

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
    loop.create_task(_run_worker_guarded(group_id, member_id, duty="queue"))


async def _run_host_guarded(group_id: str, *, phase_followup: bool = False) -> None:
    try:
        before = get_group_studio_snapshot(group_id, message_limit=1) or {}
        before_status = str(before.get("status") or "")
        await run_host_discussion(group_id)
        queue = get_queue(group_id)
        if queue:
            await _run_worker_guarded(group_id, queue[0], duty="queue")
            return
        claim = get_claim_round(group_id)
        if claim:
            await _run_worker_guarded(group_id, claim[0], duty="claim")
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
        logger.exception("群聊 Host turn crashed: group_id=%s", group_id)
        snapshot = get_group_studio_snapshot(group_id, message_limit=1) or {}
        run_id = snapshot.get("currentRunId")
        if run_id:
            update_group_studio_run_status(str(run_id), "failed", "host turn crashed")
            set_group_studio_current_run_id(group_id, None)
        emit_envelope(
            group_id=group_id,
            event_type="GROUP_ERROR",
            payload={"code": "host_failed", "message": "host turn crashed"},
        )


async def _run_worker_guarded(group_id: str, member_id: str, *, duty: str = "queue") -> None:
    try:
        await run_worker_turn(group_id, member_id, duty=duty)
        if duty == "claim":
            pop_unrunnable_claim(group_id)
            remaining_claim = get_claim_round(group_id)
            if remaining_claim:
                await _run_worker_guarded(group_id, remaining_claim[0], duty="claim")
                return
            enqueue_claimed_assignees(group_id)
        pop_unrunnable_head(group_id)
        remaining = get_queue(group_id)
        if remaining:
            await _run_worker_guarded(group_id, remaining[0], duty="queue")
            return
        leftover_claim = get_claim_round(group_id)
        if leftover_claim:
            await _run_worker_guarded(group_id, leftover_claim[0], duty="claim")
            return
        emit_envelope(
            group_id=group_id,
            event_type="GROUP_SPEAKER_QUEUE",
            payload={"memberIds": [], "kind": "drained", "pendingMemberId": None},
        )
        schedule_host_turn(group_id)
    except Exception:
        logger.exception(
            "群聊 Worker turn crashed: group_id=%s member_id=%s duty=%s",
            group_id,
            member_id,
            duty,
        )
        snapshot = get_group_studio_snapshot(group_id, message_limit=1) or {}
        run_id = snapshot.get("currentRunId")
        if run_id:
            update_group_studio_run_status(str(run_id), "failed", "worker turn crashed")
            set_group_studio_current_run_id(group_id, None)
            emit_envelope(
                group_id=group_id,
                event_type="GROUP_RUN_ENDED",
                run_id=str(run_id),
                payload={"runId": str(run_id), "status": "failed"},
            )
        pop_unrunnable_head(group_id)
        pop_unrunnable_claim(group_id)
        emit_envelope(
            group_id=group_id,
            event_type="GROUP_ERROR",
            payload={"code": "worker_failed", "message": "worker turn crashed"},
        )
