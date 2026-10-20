"""TeamRunService：Team Runtime 生命周期入口。"""

import asyncio
from collections.abc import AsyncGenerator
from typing import Any

from src.server.routes.chat.background_runs import current_chat_background_run_id
from src.server.routes.chat.completion import finalize_failed_leader_run, finalize_successful_leader_run
from src.server.routes.chat.helpers import source_event
from src.server.routes.chat.models import ChatMessage
from src.server.routes.chat.run_context import ChatRunContext
from src.server.routes.chat.team_runtime.coordinator import TeamCoordinator
from src.server.routes.chat.team_runtime.events import team_source_event
from src.server.routes.chat.team_runtime.member_runtime import TeamSessionError, run_leader_coordination, run_synthesis
from src.server.routes.chat.team_runtime.registry import register_team_service, unregister_team_service
from src.server.routes.chat.team_runtime.store import (
    TERMINAL_TASK_STATUSES,
    TeamEventPublisher,
    TeamMailboxChangedError,
    TeamStoreError,
    TeamStore,
)
from src.server.routes.chat.hitl import _clear_pending_interaction, _hitl_payload
from src.server.routes.chat.runtime_events import runtime_trace_source_event


async def execute_team_runtime(
    *,
    ctx: ChatRunContext,
    run_data: ChatMessage,
) -> AsyncGenerator[str, None]:
    """启动独立 TeamRunService，并把领域事件桥接到 Chat 后台 Run。"""

    queue: asyncio.Queue[str | None] = asyncio.Queue()
    service = TeamRunService(ctx=ctx, run_data=run_data, queue=queue)
    task = asyncio.create_task(service.run(), name="team-run-service")
    try:
        while True:
            event = await queue.get()
            if event is None:
                break
            yield event
        await task
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


class TeamRunService:
    """拥有 TeamRun 生命周期和事件发布队列。"""

    def __init__(self, *, ctx: ChatRunContext, run_data: ChatMessage, queue: asyncio.Queue[str | None]) -> None:
        self.ctx = ctx
        self.run_data = run_data
        self.queue = queue
        self.store: TeamStore | None = None
        self.publisher: TeamEventPublisher | None = None
        self._publish_lock = asyncio.Lock()
        self._member_waiters: dict[str, asyncio.Future[object]] = {}

    async def run(self) -> None:
        """运行 Leader、Coordinator 和独立综合阶段。"""

        final_answer = ""
        chat_run_id = current_chat_background_run_id() or ""
        try:
            self.store = TeamStore.create(
                session_id=self.ctx.session_id,
                chat_run_id=chat_run_id,
                goal=self.run_data.message,
                leader_agent_id=self.ctx.agent_id,
                leader_name=self.ctx.agent_name,
                workspace_id="",
                project_id="",
            )
            self.publisher = TeamEventPublisher(self.store)
            await register_team_service(chat_run_id, self)
            await self.flush_events()
            self.store.send_mail(
                sender="user",
                recipient="leader",
                kind="user_goal",
                subject="本轮目标",
                content=self.run_data.message,
            )
            await self.flush_events()

            await run_leader_coordination(
                store=self.store,
                ctx=self.ctx,
                run_data=self.run_data,
                flush_events=self.flush_events,
                permission_waiter=self.wait_for_member_permission,
            )
            coordinator = TeamCoordinator(
                store=self.store,
                ctx=self.ctx,
                run_data=self.run_data,
                flush_events=self.flush_events,
                permission_waiter=self.wait_for_member_permission,
            )
            while True:
                await coordinator.run()
                mailbox_version = len(self.store.mailbox())
                try:
                    final_answer = await run_synthesis(
                        store=self.store,
                        ctx=self.ctx,
                        run_data=self.run_data,
                        flush_events=self.flush_events,
                        on_output_delta=self._publish_final_answer_delta,
                    )
                except (TeamStoreError, TeamSessionError):
                    # 只有还有未结束任务（用户邮件触发的 rework 等）才回到 coordinator；
                    # 已彻底失败的任务是部分综合的一部分，不能再循环。
                    if any(task.get("status") not in TERMINAL_TASK_STATUSES for task in self.store.tasks()):
                        continue
                    raise
                try:
                    self.store.complete_run(expected_mailbox_version=mailbox_version)
                except TeamMailboxChangedError:
                    continue
                else:
                    break
            await self.flush_events()
            await self.queue.put(source_event("FINAL_ANSWER_REPLACE", {"content": final_answer}))
            async for event in finalize_successful_leader_run(
                session_id=self.ctx.session_id,
                persist=self.ctx.persist_run,
                agent_id=self.ctx.agent_id,
                agent_name=self.ctx.agent_name,
                model_name=self.ctx.model_name,
                user_message=self.ctx.effective_user_message,
                assistant_text=final_answer,
                assistant_thinking="",
                assistant_tool_calls=[],
                assistant_model_caches=[],
                runtime_events=[],
                skill_runs=self.ctx.skill_runs,
                active_skill_run=self.ctx.active_skill_run,
                runtime_context=self.ctx.runtime_context,
                sandbox=self.run_data.sandbox,
            ):
                await self.queue.put(event)
        except asyncio.CancelledError:
            if self.store is not None and self.store.run().get("status") == "running":
                self.store.cancel_run()
                await self.flush_events()
            raise
        except Exception as exc:
            from src.exception_detail import format_agent_exception

            reason = format_agent_exception(exc, prefix="Team Runtime 执行失败")
            if self.store is not None:
                self.store.fail_run(reason)
                await self.flush_events()
            async for event in finalize_failed_leader_run(
                session_id=self.ctx.session_id,
                persist=self.ctx.persist_run,
                agent_id=self.ctx.agent_id,
                agent_name=self.ctx.agent_name,
                model_name=self.ctx.model_name,
                assistant_text=final_answer,
                assistant_error=reason,
                assistant_model_caches=[],
                skill_runs=self.ctx.skill_runs,
                sandbox=self.run_data.sandbox,
            ):
                await self.queue.put(event)
        finally:
            for interaction_id, future in self._member_waiters.items():
                if not future.done():
                    future.cancel()
                _clear_pending_interaction(interaction_id)
            self._member_waiters.clear()
            await unregister_team_service(chat_run_id, self)
            await self.queue.put(None)

    async def wait_for_member_permission(self, interaction: Any) -> object:
        """登记成员审批后挂起该成员协程，审批期间不结束 TeamRun。"""

        loop = asyncio.get_running_loop()
        future: asyncio.Future[object] = loop.create_future()
        self._member_waiters[str(interaction.id)] = future
        try:
            self.store.record_member_event(
                "member.permission.required",
                member_id=str(interaction.agent_id),
                task_id=str((interaction.chat_data or {}).get("teamTaskId") or ""),
                status="waiting",
                payload={"interaction": _hitl_payload(interaction)},
            )
            await self.flush_events()
            await self.queue.put(self._permission_signal("RUN_WAITING", interaction, status="waiting_permission"))
            return await future
        finally:
            self._member_waiters.pop(str(interaction.id), None)
            _clear_pending_interaction(interaction.id)

    async def resolve_member_permission(self, interaction_id: str, resume_event: object, *, action: str) -> None:
        """把 Chat resume 的精确结果交回等待中的 Team Member。"""

        future = self._member_waiters.get(str(interaction_id))
        if future is None or future.done():
            raise RuntimeError("Team 成员审批已失效或已恢复")
        from src.server.routes.chat.models import get_pending_interaction

        interaction = get_pending_interaction(interaction_id)
        self.store.record_member_event(
            "member.permission.resolved",
            member_id=interaction.agent_id,
            task_id=str((interaction.chat_data or {}).get("teamTaskId") or ""),
            status="running",
            payload={"interactionId": interaction_id, "action": action},
        )
        await self.flush_events()
        await self.queue.put(self._permission_signal("HITL_RESOLVED", interaction,
                                                    status="completed" if action == "approve" else "rejected",
                                                    action=action))
        await self.queue.put(self._permission_signal("RUN_RESUMED", interaction, status="running", action=action))
        # 其他成员仍可能等待批准；顶层状态不能被这一次批准永久改成 running。
        if any(key != interaction_id and not item.done() for key, item in self._member_waiters.items()):
            await self.queue.put(self._permission_signal("RUN_WAITING", interaction, status="waiting_permission"))
        future.set_result(resume_event)

    def _permission_signal(self, event_type: str, interaction: Any, *, status: str, action: str = ""):
        """状态和审批 Trace 与成员领域事实使用同一 Chat Run。"""

        return runtime_trace_source_event(
            event_type, session_id=interaction.session_id, run_id=interaction.run_id,
            scope="team" if event_type == "HITL_RESOLVED" else "run",
            agent_id=interaction.agent_id, agent_name=interaction.agent_name, status=status,
            payload={"interactionId": interaction.id, "interactionType": interaction.type,
                     "action": action, "waitState": status},
        )

    def has_member_waiter(self, interaction_id: str) -> bool:
        """恢复命令先检查 live waiter，再接受持久审批。"""
        future = self._member_waiters.get(interaction_id)
        return future is not None and not future.done()

    async def flush_events(self) -> None:
        """按 seq 发布尚未发送的持久化 Team events。"""

        if self.publisher is None:
            return
        async with self._publish_lock:
            for event in self.publisher.drain():
                await self.queue.put(team_source_event(event))

    async def _publish_final_answer_delta(self, delta: str) -> None:
        """把综合 session 的模型增量同步投影到 Chat 最终回答。"""

        if delta:
            await self.queue.put(source_event("FINAL_ANSWER_DELTA", {"delta": delta}))

    async def send_user_mail(
        self,
        *,
        recipient: str,
        content: str,
        dispatch_mode: str = "context",
        task_id: str = "",
    ) -> dict:
        """用户向当前 active TeamRun 成员投递干预消息。"""

        if self.store is None or self.store.run().get("status") != "running":
            raise RuntimeError("TeamRun 当前不可接收消息")
        normalized = content.strip()
        if not normalized:
            raise RuntimeError("Mailbox 消息不能为空")
        if dispatch_mode not in {"context", "continue_task", "new_task"}:
            raise RuntimeError("不支持的 Mailbox dispatchMode")
        if dispatch_mode != "context" and recipient in {"broadcast", "*"}:
            raise RuntimeError("执行型 Mailbox 消息必须指定单个 Member")
        if recipient in {"broadcast", "*"}:
            message: dict | list[dict] = self.store.broadcast_mail(
                sender="user",
                kind="user_intervention",
                subject="用户广播",
                content=normalized,
                dispatch_mode=dispatch_mode,
            )
        else:
            message = self.store.send_mail(
                sender="user",
                recipient=recipient,
                kind="user_intervention",
                subject="用户补充",
                content=normalized,
                task_id=task_id,
                dispatch_mode=dispatch_mode,
            )
            member = self.store.member(recipient)
            if dispatch_mode == "new_task":
                self.store.create_task(
                    title="用户追加任务",
                    brief=normalized,
                    created_by="user",
                    assigned_member_id=recipient,
                    origin="user_mail",
                    origin_mail_id=str(message["id"]),
                )
            elif dispatch_mode == "continue_task" and member.get("status") == "idle":
                target_task_id = task_id or str(member.get("lastTaskId") or "")
                if not target_task_id:
                    raise RuntimeError("该 Member 没有可继续执行的历史任务")
                previous = self.store.task(target_task_id)
                self.store.create_task(
                    title=f"继续执行：{previous.get('title') or target_task_id}",
                    brief=normalized,
                    created_by="user",
                    assigned_member_id=recipient,
                    depends_on=[target_task_id],
                    origin="user_mail",
                    origin_mail_id=str(message["id"]),
                )
        await self.flush_events()
        return message
