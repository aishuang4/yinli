"""POST /send — 发送消息并通过 SSE 返回 Agent 事件流。"""

import uuid
from typing import AsyncGenerator

from fastapi.responses import StreamingResponse

from src.server.routes.chat.completion import emit_failed_startup_run
from src.server.routes.chat.initial_events import (
    emit_context_events,
    emit_start_events,
    persist_user_message_and_trace,
)
from src.server.routes.chat.background_runs import (
    start_chat_background_run,
    serialize_chat_background_run,
    subscribe_chat_background_run,
)
from src.server.routes.chat.models import ChatMessage
from src.server.routes.chat.router import router
from src.server.routes.chat.run_context import build_chat_run_context
from src.server.routes.chat.runtime_dispatcher import execute_chat_runtime
from src.server.routes.chat.startup_checks import get_startup_error


@router.post("/send")
async def send_message(data: ChatMessage) -> StreamingResponse:
    """
    发送消息并通过 SSE 返回 Agent 事件流

    调用流程：
    1. 标准化 session 并启动后台 Run
    2. 构建本轮 ChatRunContext
    3. 输出开场事件和启动校验
    4. 委托 main_agent_flow 执行主 Agent 流程

    Args:
        data: 包含 session_id、message、agent_id 等的请求体

    Returns:
        StreamingResponse: SSE 事件流（text/event-stream）
    """

    run = await start_chat_run(data)
    return StreamingResponse(
        subscribe_chat_background_run(run.id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/send/start")
async def start_message(data: ChatMessage) -> dict:
    """创建后台 Run 并返回元数据，前端随后订阅 RunState stream。"""

    run = await start_chat_run(data)
    return {"run": serialize_chat_background_run(run)}


async def start_chat_run(data: ChatMessage):
    """创建后台 Chat Run。"""

    session_id = data.session_id or uuid.uuid4().hex
    run_data = data.model_copy(update={"session_id": session_id})

    async def event_stream() -> AsyncGenerator[str, None]:
        """
        生成 SSE 事件流

        将 AgentScope 2.0 的 AgentEvent 转换为 SSE 格式：
          event: <EventType>
          data: <JSON>
        """
        ctx = await build_chat_run_context(run_data)
        persist_user_message_and_trace(ctx, run_data)
        async for start_event in emit_start_events(ctx, run_data):
            yield start_event
        async for context_event in emit_context_events(ctx):
            yield context_event

        assistant_error = get_startup_error(ctx, run_data)
        if assistant_error:
            async for failure_event in emit_failed_startup_run(
                session_id=ctx.session_id,
                persist=ctx.persist_run,
                agent_id=ctx.agent_id,
                agent_name=ctx.agent_name,
                model_name=ctx.model_name,
                assistant_error=assistant_error,
                skill_runs=ctx.skill_runs,
                team_activities=ctx.team_activities,
            ):
                yield failure_event
            return

        async for main_event in execute_chat_runtime(ctx=ctx, run_data=run_data):
            yield main_event

    return await start_chat_background_run(
        session_id=session_id,
        factory=event_stream,
        agent_id=run_data.agent_id or "default",
        agent_name=run_data.agent_name or "",
        team_id=run_data.team_id or "",
        team_name=run_data.team_name or "",
        task=run_data.message,
        mode="team" if run_data.team_id else "agent",
    )
