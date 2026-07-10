"""Team Runtime 事件驱动并发协调器。"""

import asyncio
from collections.abc import Awaitable, Callable

from src.server.routes.chat.models import ChatMessage
from src.server.routes.chat.run_context import ChatRunContext
from src.server.routes.chat.team_runtime.member_runtime import run_member_task
from src.server.routes.chat.team_runtime.store import TeamStore, TeamStoreError


FlushEvents = Callable[[], Awaitable[None]]


class TeamCoordinatorError(RuntimeError):
    """共享任务图无法继续推进。"""


class TeamCoordinator:
    """根据 Task List 状态并发运行独立 Member session。"""

    def __init__(
        self,
        *,
        store: TeamStore,
        ctx: ChatRunContext,
        run_data: ChatMessage,
        flush_events: FlushEvents,
    ) -> None:
        self.store = store
        self.ctx = ctx
        self.run_data = run_data
        self.flush_events = flush_events
        self.max_concurrency = max(1, min(int(store.run().get("maxConcurrency") or 3), 8))

    async def run(self) -> None:
        """运行到全部任务成功或明确失败。"""

        active: dict[str, asyncio.Task[None]] = {}
        try:
            while True:
                tasks = self.store.tasks()
                failed = [task for task in tasks if task.get("status") == "failed"]
                if failed:
                    raise TeamCoordinatorError(
                        "任务执行失败: " + "、".join(str(task.get("title") or task["id"]) for task in failed)
                    )
                if tasks and all(task.get("status") == "succeeded" for task in tasks):
                    return

                while len(active) < self.max_concurrency:
                    assignment = self._next_assignment(set(active))
                    if assignment is None:
                        break
                    task, member = assignment
                    claimed = self.store.claim_task(str(task["id"]), str(member["id"]))
                    await self.flush_events()
                    active[str(claimed["id"])] = asyncio.create_task(
                        self._execute(claimed, member),
                        name=f"team-task-{claimed['id']}",
                    )

                if not active:
                    queued = [task for task in self.store.tasks() if task.get("status") == "queued"]
                    if queued:
                        blocked = [task for task in queued if self.store.is_blocked(task)]
                        if blocked:
                            raise TeamCoordinatorError(
                                "任务依赖无法推进: " + "、".join(str(task.get("title") or task["id"]) for task in blocked)
                            )
                        raise TeamCoordinatorError("存在可执行任务，但没有可用 Member")
                    raise TeamCoordinatorError("Task List 没有形成可综合的成功结果")

                done, _ = await asyncio.wait(active.values(), return_when=asyncio.FIRST_COMPLETED)
                for task_id, future in list(active.items()):
                    if future not in done:
                        continue
                    active.pop(task_id, None)
                    await future
        finally:
            for future in active.values():
                if not future.done():
                    future.cancel()
            if active:
                await asyncio.gather(*active.values(), return_exceptions=True)

    def _next_assignment(self, active_task_ids: set[str]) -> tuple[dict, dict] | None:
        ready = [task for task in self.store.claimable_tasks() if str(task.get("id") or "") not in active_task_ids]
        idle_members = [
            member
            for member in self.store.members()
            if member.get("role") != "leader" and member.get("status") == "idle" and not member.get("activeTaskId")
        ]
        idle_by_id = {str(member["id"]): member for member in idle_members}
        for task in ready:
            assigned = str(task.get("assignedMemberId") or "")
            if assigned:
                member = idle_by_id.get(assigned)
                if member:
                    return task, member
                continue
            if idle_members:
                return task, idle_members[0]
        return None

    async def _execute(self, task: dict, member: dict) -> None:
        task_id = str(task["id"])
        member_id = str(member["id"])
        try:
            await run_member_task(
                store=self.store,
                ctx=self.ctx,
                run_data=self.run_data,
                member=member,
                task=task,
                flush_events=self.flush_events,
            )
            self.store.set_member_status(member_id, "idle")
            await self.flush_events()
        except asyncio.CancelledError:
            self.store.cancel_task(task_id, member_id, "Member task cancelled")
            self.store.set_member_status(member_id, "stopped")
            await self.flush_events()
            raise
        except (TeamStoreError, Exception) as exc:
            self.store.fail_task(task_id, member_id, str(exc))
            self.store.set_member_status(member_id, "failed")
            await self.flush_events()
