"""发言调度：人类点名后只串行跑 Worker，说完停。"""

from __future__ import annotations

import asyncio
import logging
import os

from src.group_studio.events import emit_envelope
from src.group_studio.speaker_queue import get_queue, pop_unrunnable_head, set_queue
from src.group_studio.worker_runtime import run_worker_turn
from src.storage.database import (
    get_group_studio_snapshot,
    set_group_studio_current_run_id,
    update_group_studio_run_status,
)

logger = logging.getLogger(__name__)


def should_auto_run() -> bool:
    """单测默认不点火，避免 from-chat / 发消息测试触发模型。"""

    return not os.environ.get("PYTEST_CURRENT_TEST")


def schedule_next(group_id: str) -> None:
    """只派队列里的下一个 Worker。队列空则 drained 后停。"""

    snapshot = get_group_studio_snapshot(group_id, message_limit=1)
    if snapshot is None or snapshot.get("currentRunId"):
        return
    if str(snapshot.get("status") or "") != "open":
        return
    pop_unrunnable_head(group_id)
    queue = get_queue(group_id)
    if not queue:
        return
    if not should_auto_run():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(_run_worker_guarded(group_id, queue[0]))


def schedule_worker_turn(group_id: str, member_id: str) -> None:
    """显式启动指定工人一轮。"""

    snapshot = get_group_studio_snapshot(group_id, message_limit=1)
    if snapshot is None or snapshot.get("currentRunId"):
        return
    if str(snapshot.get("status") or "") != "open":
        return
    if not should_auto_run():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(_run_worker_guarded(group_id, member_id))


async def _run_worker_guarded(group_id: str, member_id: str) -> None:
    """跑一个工人；崩溃只记本轮失败，队列继续。"""

    try:
        await run_worker_turn(group_id, member_id, duty="queue")
        pop_unrunnable_head(group_id)
    except Exception as exc:
        from src.exception_detail import format_agent_exception

        message = format_agent_exception(exc, prefix="群聊 Worker 执行失败")
        logger.error("群聊 Worker turn crashed: group_id=%s member_id=%s\n%s", group_id, member_id, message)
        snapshot = get_group_studio_snapshot(group_id, message_limit=1) or {}
        run_id = snapshot.get("currentRunId")
        if run_id:
            update_group_studio_run_status(str(run_id), "failed", message)
            set_group_studio_current_run_id(group_id, None)
            emit_envelope(
                group_id=group_id,
                event_type="GROUP_RUN_ENDED",
                run_id=str(run_id),
                payload={"runId": str(run_id), "status": "failed"},
            )
        pop_unrunnable_head(group_id)
        emit_envelope(
            group_id=group_id,
            event_type="GROUP_ERROR",
            payload={"code": "worker_failed", "message": message, "memberId": member_id},
        )
    await _continue_after_worker(group_id)


async def _continue_after_worker(group_id: str) -> None:
    """一个 Worker 结束后：还有人就继续，否则 drained 停。"""

    try:
        remaining = get_queue(group_id)
        if remaining:
            await _run_worker_guarded(group_id, remaining[0])
            return
        set_queue(group_id, [], kind="drained")
    except Exception:
        logger.exception("群聊 Worker 队列续跑失败: group_id=%s", group_id)
