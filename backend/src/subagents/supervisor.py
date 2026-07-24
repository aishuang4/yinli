"""单进程 Subagent task 执行句柄管理。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable


class SubagentSupervisor:
    """管理当前进程的后台 task，不作为持久状态事实源。"""

    def __init__(self, *, max_concurrency: int = 2) -> None:
        self._lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(max(1, max_concurrency))
        self._tasks: dict[str, asyncio.Task[object]] = {}
        self._cancel_handlers: dict[str, Callable[[], Awaitable[None]]] = {}

    async def start(
        self,
        invocation_id: str,
        operation: Callable[[], Awaitable[object]],
        on_cancel: Callable[[], Awaitable[None]] | None = None,
    ) -> asyncio.Task[object]:
        """注册并启动一个 task；同一 invocation 只能有一个活动句柄。"""

        async with self._lock:
            existing = self._tasks.get(invocation_id)
            if existing is not None and not existing.done():
                raise ValueError(f"Subagent task is already active: {invocation_id}")

            async def run() -> object:
                try:
                    async with self._semaphore:
                        return await operation()
                except asyncio.CancelledError:
                    if on_cancel is not None:
                        await on_cancel()
                    raise

            task = asyncio.create_task(run(), name=f"subagent:{invocation_id}")
            self._tasks[invocation_id] = task
            if on_cancel is not None:
                self._cancel_handlers[invocation_id] = on_cancel
            task.add_done_callback(lambda _: asyncio.create_task(self._remove(invocation_id, task)))
            return task

    async def cancel(self, invocation_id: str) -> bool:
        """请求取消当前进程仍持有的执行句柄。"""

        async with self._lock:
            task = self._tasks.get(invocation_id)
            if task is None or task.done():
                return False
            on_cancel = self._cancel_handlers.get(invocation_id)
            task.cancel()
        if on_cancel is not None:
            await on_cancel()
        return True

    async def active_invocation_ids(self) -> set[str]:
        """返回当前进程内仍未完成的 invocation id。"""

        async with self._lock:
            return {invocation_id for invocation_id, task in self._tasks.items() if not task.done()}

    async def _remove(self, invocation_id: str, completed: asyncio.Task[object]) -> None:
        async with self._lock:
            if self._tasks.get(invocation_id) is completed:
                self._tasks.pop(invocation_id, None)
                self._cancel_handlers.pop(invocation_id, None)


_SUPERVISOR = SubagentSupervisor()


def get_subagent_supervisor() -> SubagentSupervisor:
    """获取当前后端进程唯一的 Subagent Supervisor。"""

    return _SUPERVISOR
