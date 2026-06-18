"""后台 Chat Run 管理。

把 Agent 执行与前端 SSE 订阅解耦：后端创建后台 task 持续执行 Agent，
前端连接只负责订阅事件。页面切换、刷新或网络断开不会直接取消后台执行。
"""

import asyncio
import uuid
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from src.server.routes.chat.helpers import _sse_event


EventStreamFactory = Callable[[], AsyncGenerator[str, None]]
MAX_BACKGROUND_RUNS = 50
MAX_EVENTS_PER_RUN = 1200
COMPLETED_RUN_TTL = timedelta(hours=2)


@dataclass
class ChatBackgroundRun:
    """进程内后台 Run 状态。"""

    id: str
    session_id: str
    status: str = "running"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: str | None = None
    events: list[str] = field(default_factory=list)
    subscribers: set[asyncio.Queue[str | None]] = field(default_factory=set)
    task: asyncio.Task | None = None


_runs: dict[str, ChatBackgroundRun] = {}
_runs_lock = asyncio.Lock()


async def get_chat_background_run(run_id: str) -> ChatBackgroundRun | None:
    """读取后台 Run 状态。"""
    async with _runs_lock:
        return _runs.get(run_id)


async def list_chat_background_runs() -> list[ChatBackgroundRun]:
    """列出当前进程内后台 Run。"""
    async with _runs_lock:
        return sorted(_runs.values(), key=lambda item: item.created_at, reverse=True)


async def start_chat_background_run(
    *,
    session_id: str,
    factory: EventStreamFactory,
) -> ChatBackgroundRun:
    """创建后台 Run 并启动执行 task。"""
    run = ChatBackgroundRun(id=f"run-{uuid.uuid4().hex}", session_id=session_id)
    async with _runs_lock:
        _prune_runs_locked()
        _runs[run.id] = run
    run.task = asyncio.create_task(_consume_chat_events(run, factory), name=f"chat-run-{run.id}")
    return run


async def subscribe_chat_background_run(run_id: str) -> AsyncGenerator[str, None]:
    """订阅后台 Run 事件；订阅断开不会取消后台 task。"""
    async with _runs_lock:
        run = _runs.get(run_id)
        if run is None:
            error_event = _sse_event("ERROR", {"message": f"Run not found: {run_id}"})
            replay_events: list[str] = [error_event]
            queue = None
            already_finished = True
        else:
            replay_events = list(run.events)
            queue: asyncio.Queue[str | None] | None = asyncio.Queue()
            run.subscribers.add(queue)
            already_finished = run.status in {"completed", "failed", "cancelled"}

    try:
        for event in replay_events:
            yield event
        if already_finished or queue is None:
            return
        while True:
            event = await queue.get()
            if event is None:
                return
            yield event
    finally:
        async with _runs_lock:
            current = _runs.get(run_id)
            if current is not None and queue is not None:
                current.subscribers.discard(queue)


async def _consume_chat_events(run: ChatBackgroundRun, factory: EventStreamFactory) -> None:
    await _broadcast(run, _sse_event("RUN", {
        "run_id": run.id,
        "session_id": run.session_id,
        "status": run.status,
        "created_at": run.created_at,
    }))
    try:
        async for event in factory():
            await _broadcast(run, event)
        run.status = "completed"
    except asyncio.CancelledError:
        run.status = "cancelled"
        await _broadcast(run, _sse_event("ERROR", {
            "message": "Run cancelled",
            "run_id": run.id,
            "session_id": run.session_id,
        }))
        raise
    except Exception as exc:
        run.status = "failed"
        await _broadcast(run, _sse_event("ERROR", {
            "message": f"Run failed: {exc}",
            "run_id": run.id,
            "session_id": run.session_id,
        }))
    finally:
        run.completed_at = datetime.now(timezone.utc).isoformat()
        await _close_subscribers(run)
        async with _runs_lock:
            _prune_runs_locked()


async def _broadcast(run: ChatBackgroundRun, event: str) -> None:
    run.events.append(event)
    if len(run.events) > MAX_EVENTS_PER_RUN:
        run.events = run.events[-MAX_EVENTS_PER_RUN:]
    for queue in list(run.subscribers):
        await queue.put(event)


async def _close_subscribers(run: ChatBackgroundRun) -> None:
    for queue in list(run.subscribers):
        await queue.put(None)


def _prune_runs_locked() -> None:
    """Drop old completed in-memory runs while keeping active runs available."""

    now = datetime.now(timezone.utc)
    removable_ids: list[str] = []
    for run_id, run in _runs.items():
        if run.status == "running" or run.subscribers:
            continue
        if run.completed_at:
            try:
                completed_at = datetime.fromisoformat(run.completed_at)
            except ValueError:
                completed_at = now
            if now - completed_at > COMPLETED_RUN_TTL:
                removable_ids.append(run_id)

    for run_id in removable_ids:
        _runs.pop(run_id, None)

    if len(_runs) <= MAX_BACKGROUND_RUNS:
        return

    candidates = sorted(
        [
            run
            for run in _runs.values()
            if run.status != "running" and not run.subscribers
        ],
        key=lambda item: item.completed_at or item.created_at,
    )
    for run in candidates[: max(0, len(_runs) - MAX_BACKGROUND_RUNS)]:
        _runs.pop(run.id, None)
