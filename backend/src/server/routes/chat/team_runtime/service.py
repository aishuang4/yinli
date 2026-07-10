"""TeamRunService：Team Runtime 生命周期入口。"""

import asyncio
from collections.abc import AsyncGenerator

from src.server.routes.chat.background_runs import current_chat_background_run_id
from src.server.routes.chat.completion import finalize_failed_leader_run, finalize_successful_leader_run
from src.server.routes.chat.helpers import _sse_event
from src.server.routes.chat.models import ChatMessage
from src.server.routes.chat.run_context import ChatRunContext
from src.server.routes.chat.team_runtime.coordinator import TeamCoordinator
from src.server.routes.chat.team_runtime.events import team_event_sse
from src.server.routes.chat.team_runtime.member_runtime import run_leader_coordination, run_synthesis
from src.server.routes.chat.team_runtime.registry import register_team_service, unregister_team_service
from src.server.routes.chat.team_runtime.store import (
    TeamEventPublisher,
    TeamMailboxChangedError,
    TeamStore,
)


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
            )
            coordinator = TeamCoordinator(
                store=self.store,
                ctx=self.ctx,
                run_data=self.run_data,
                flush_events=self.flush_events,
            )
            await coordinator.run()
            while True:
                mailbox_version = len(self.store.mailbox())
                final_answer = await run_synthesis(
                    store=self.store,
                    ctx=self.ctx,
                    run_data=self.run_data,
                    flush_events=self.flush_events,
                )
                try:
                    self.store.complete_run(expected_mailbox_version=mailbox_version)
                except TeamMailboxChangedError:
                    pass
                else:
                    break
            await self.flush_events()
            await self.queue.put(_sse_event("FINAL_ANSWER_REPLACE", {"content": final_answer}))
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
                sandbox=self.run_data.sandbox,
            ):
                await self.queue.put(event)
        except asyncio.CancelledError:
            if self.store is not None and self.store.run().get("status") == "running":
                self.store.cancel_run()
                await self.flush_events()
            raise
        except Exception as exc:
            reason = f"Team Runtime 执行失败: {exc}"
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
            await unregister_team_service(chat_run_id, self)
            await self.queue.put(None)

    async def flush_events(self) -> None:
        """按 seq 发布尚未发送的持久化 Team events。"""

        if self.publisher is None:
            return
        async with self._publish_lock:
            for event in self.publisher.drain():
                await self.queue.put(team_event_sse(event))

    async def send_user_mail(self, *, recipient: str, content: str) -> dict:
        """用户向当前 active TeamRun 成员投递干预消息。"""

        if self.store is None or self.store.run().get("status") != "running":
            raise RuntimeError("TeamRun 当前不可接收消息")
        normalized = content.strip()
        if not normalized:
            raise RuntimeError("Mailbox 消息不能为空")
        if recipient in {"broadcast", "*"}:
            message: dict | list[dict] = self.store.broadcast_mail(
                sender="user",
                kind="user_intervention",
                subject="用户广播",
                content=normalized,
            )
        else:
            message = self.store.send_mail(
                sender="user",
                recipient=recipient,
                kind="user_intervention",
                subject="用户补充",
                content=normalized,
            )
        await self.flush_events()
        return message
