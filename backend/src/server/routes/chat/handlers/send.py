"""POST /send — 发送消息并通过 SSE 返回 Agent 事件流。"""

import uuid
from typing import AsyncGenerator

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from src.server.routes.chat.completion import emit_failed_startup_run
from src.server.routes.chat.initial_events import (
    emit_context_events,
    emit_start_events,
    persist_user_message_and_trace,
)
from src.server.routes.chat.background_runs import (
    current_chat_background_run_id,
    start_chat_background_run,
    serialize_chat_background_run,
    subscribe_chat_background_run,
)
from src.project_files import validate_session_uploads
from src.runtime_context import resolve_runtime_context
from src.server.routes.chat.models import ChatMessage
from src.server.routes.chat.router import router
from src.server.routes.chat.run_context import build_chat_run_context
from src.server.routes.chat.runtime_dispatcher import execute_chat_runtime
from src.server.routes.chat.startup_checks import get_startup_error
from src.server.routes.chat.projection.source_events import SourceEvent
from src.config.product_features import is_surface_enabled
from src.config.runtime_settings import resolve_model_binding
from src.server.routes.chat.store import _load_store


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
    """创建后台 Run 并返回元数据，前端随后订阅 RuntimeFrame stream。"""

    run = await start_chat_run(data)
    return {"run": serialize_chat_background_run(run)}


async def start_chat_run(data: ChatMessage):
    """创建后台 Chat Run。"""

    if bool(data.plan_action) != bool(data.plan_id):
        raise HTTPException(status_code=422, detail="plan_action 和 plan_id 必须同时提供。")
    if data.plan_action and data.mode != "agent":
        raise HTTPException(status_code=422, detail="Plan 审核与执行只支持单 Agent 模式。")
    if data.plan_action == "revise" and not data.plan_feedback.strip():
        raise HTTPException(status_code=422, detail="修改计划必须提供自然语言反馈。")
    if data.mode == "team" and not is_surface_enabled("teamMode"):
        raise HTTPException(status_code=403, detail="team_mode_disabled")
    if data.plan_requested and not data.plan_action and not is_surface_enabled("planMode"):
        raise HTTPException(status_code=403, detail="plan_mode_disabled")
    session_id = data.session_id or uuid.uuid4().hex
    try:
        runtime_context = resolve_runtime_context(
            project_id=data.project_id,
            session_id=session_id,
            run_id="",
            agent_id=data.agent_id or "default",
        )
        session = next((s for s in _load_store().sessions if s.id == session_id), None)
        binding = resolve_model_binding(data.model_config_id or (session.model_config_id if session else None))
        if data.images and not binding.primary.entry.capabilities.images:
            raise ValueError("所选模型未启用图片能力，请切换模型。")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    run_data = data.model_copy(update={
        "session_id": session_id,
        "project_id": runtime_context.project_id,
        "model_config_id": binding.primary.entry.id,
    })
    try:
        validate_session_uploads(
            project_root=runtime_context.project_root,
            session_id=session_id,
            paths=[item.path for item in run_data.files],
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    async def event_stream() -> AsyncGenerator[SourceEvent, None]:
        """
        生成进程内结构化源流；SSE 只在最终 Projection Stream 边界序列化。
        """
        ctx = await build_chat_run_context(
            run_data,
            runtime_context.with_run_id(current_chat_background_run_id()),
        )
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
        task=run_data.message,
        mode=run_data.mode,
        workspace_id=runtime_context.workspace_id,
        project_id=runtime_context.project_id,
        model_binding=binding,
    )
