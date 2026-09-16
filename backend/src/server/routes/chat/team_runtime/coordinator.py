"""Team Runtime 事件驱动并发协调器。"""

import asyncio
from collections.abc import Awaitable, Callable

from src.server.routes.chat.models import ChatMessage
from src.server.routes.chat.run_context import ChatRunContext
from src.server.routes.chat.team_runtime.member_runtime import run_member_task
from src.server.routes.chat.team_runtime.store import TERMINAL_TASK_STATUSES, TeamStore, TeamStoreError


FlushEvents = Callable[[], Awaitable[None]]

# 单个 Member 任务的墙钟上限：超过即判失败（可重派一次），避免一个卡死的 session 拖住整轮。
TASK_TIMEOUT_SECONDS = 10 * 60


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
        """运行到全部任务结束。

        失败任务先自动重派一次（新 session、带失败原因）；仍失败的任务保留 failed，并把
        依赖它的任务级联失败。只要至少有一个任务成功就正常返回，由综合阶段做部分综合；
        全部失败才抛错。
        """

        active: dict[str, asyncio.Task[None]] = {}
        try:
            while True:
                self._recover_stale_members(set(active))
                tasks = self.store.tasks()
                if self._retry_or_cascade_failures(tasks, active_task_ids=set(active)):
                    await self.flush_events()
                    tasks = self.store.tasks()
                if tasks and not active and all(task.get("status") in TERMINAL_TASK_STATUSES for task in tasks):
                    if any(task.get("status") == "succeeded" for task in tasks):
                        return
                    failed = [task for task in tasks if task.get("status") == "failed"]
                    raise TeamCoordinatorError(
                        "任务全部失败: " + "、".join(
                            f"{task.get('title') or task['id']}（{task.get('failureReason') or '未知原因'}）"
                            for task in failed
                        )
                    )

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
                        claimable = [task for task in queued if not self.store.is_blocked(task)]
                        if claimable:
                            raise TeamCoordinatorError(
                                "存在可执行任务，但没有可用 Member: "
                                + "、".join(str(task.get("title") or task["id"]) for task in claimable)
                            )
                        raise TeamCoordinatorError(
                            "任务依赖无法推进: "
                            + "、".join(str(task.get("title") or task["id"]) for task in queued)
                        )
                    if not self.store.tasks():
                        raise TeamCoordinatorError("Task List 没有形成可综合的成功结果")
                    orphans = [task for task in self.store.tasks() if task.get("status") == "running"]
                    if orphans:
                        for task in orphans:
                            self.store.fail_task(
                                str(task["id"]),
                                str(task.get("ownerMemberId") or ""),
                                "任务执行器已丢失，已标记失败",
                            )
                        await self.flush_events()
                    continue

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

    def _retry_or_cascade_failures(self, tasks: list[dict], *, active_task_ids: set[str]) -> bool:
        """处理刚失败的任务：可重试的重新排队，不可重试的把下游任务级联失败。返回是否有改动。"""

        changed = False
        for task in tasks:
            if task.get("status") != "failed":
                continue
            if self.store.can_retry_task(task):
                self.store.retry_failed_task(str(task["id"]))
                changed = True
        tasks = self.store.tasks()
        broken_dep_ids = {
            str(task["id"])
            for task in tasks
            if task.get("status") in {"failed", "cancelled"}
        }
        if not broken_dep_ids:
            return changed
        for task in tasks:
            task_id = str(task["id"])
            if task.get("status") != "queued" or task_id in active_task_ids:
                continue
            broken = [dep for dep in (task.get("dependsOn") or []) if str(dep) in broken_dep_ids]
            if broken:
                self.store.fail_task(
                    task_id,
                    "",
                    "依赖任务已失败: " + "、".join(str(dep) for dep in broken),
                    retryable=False,
                )
                changed = True
        return changed

    def _recover_stale_members(self, active_task_ids: set[str]) -> None:
        """失败或丢失执行器的成员回到 idle，避免被钉死后整队无法继续分派。"""

        for member in self.store.members():
            if member.get("role") == "leader":
                continue
            member_id = str(member["id"])
            status = str(member.get("status") or "")
            active_task_id = str(member.get("activeTaskId") or "")
            if status == "failed" and not active_task_id:
                self.store.set_member_status(member_id, "idle")
            elif status == "running" and active_task_id and active_task_id not in active_task_ids:
                self.store.fail_task(active_task_id, member_id, "任务执行器已丢失，已标记失败")
                self.store.set_member_status(member_id, "idle")
            elif status == "running" and not active_task_id:
                self.store.set_member_status(member_id, "idle")

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
        worker = asyncio.create_task(
            run_member_task(
                store=self.store,
                ctx=self.ctx,
                run_data=self.run_data,
                member=member,
                task=task,
                flush_events=self.flush_events,
            ),
            name=f"team-member-{member_id}-{task_id}",
        )
        try:
            await asyncio.wait_for(worker, timeout=TASK_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            self.store.fail_task(task_id, member_id, f"任务超过 {TASK_TIMEOUT_SECONDS // 60} 分钟未完成")
            self.store.set_member_status(member_id, "idle")
            await self.flush_events()
        except asyncio.CancelledError:
            self.store.cancel_task(task_id, member_id, "Member task cancelled")
            self.store.set_member_status(member_id, "stopped")
            await self.flush_events()
            raise
        except (TeamStoreError, Exception) as exc:
            self.store.fail_task(task_id, member_id, str(exc))
            # 任务失败不等于成员作废；回到 idle 才能被重派或接下一个任务。
            self.store.set_member_status(member_id, "idle")
            await self.flush_events()
        else:
            self.store.set_member_status(member_id, "idle")
            await self.flush_events()
        finally:
            if not worker.done():
                worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
