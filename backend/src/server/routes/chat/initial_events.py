"""Chat Run 开场事件、Trace 和上下文事件输出。"""

from typing import AsyncGenerator

from src.server.routes.chat.helpers import _sse_event
from src.server.routes.chat.intent_router import route_trace_payload
from src.server.routes.chat.runtime_events import runtime_event_sse
from src.server.routes.chat.run_context import ChatRunContext
from src.server.routes.chat.skill_runs import update_skill_run_status
from src.server.routes.chat.store import _append_assistant_message, _append_user_message
from src.server.routes.chat.trace_recorder import (
    _record_run_event,
    _record_workspace_event,
    _workspace_trace_payload,
)


def persist_user_message_and_trace(ctx: ChatRunContext, run_data) -> None:
    """写入用户消息和 USER_MESSAGE trace。"""

    if not ctx.persist_run or not run_data.message.strip():
        return
    display_content = (run_data.display_message or run_data.message).strip()
    _append_user_message(
        session_id=ctx.session_id,
        agent_id=ctx.agent_id,
        agent_name=ctx.agent_name,
        mode=run_data.mode,
        model_name=ctx.model_name,
        content=display_content,
        images=run_data.images,
    )
    _record_run_event(
        session_id=ctx.session_id,
        event_type="USER_MESSAGE",
        persist=ctx.persist_run,
        agent_id=ctx.agent_id,
        agent_name=ctx.agent_name,
        status="completed",
        title="User message",
        content=display_content,
        payload={
            "mode": run_data.mode,
            "model": ctx.model_name,
            "imageCount": len(run_data.images),
        },
    )


async def emit_start_events(ctx: ChatRunContext, run_data) -> AsyncGenerator[str, None]:
    """输出 SESSION 事件并记录本轮启动 trace。"""

    yield runtime_event_sse(
        "RUN_STARTED",
        session_id=ctx.session_id,
        scope="run",
        agent_id=ctx.agent_id,
        agent_name=ctx.agent_name,
        status="running",
        payload={
            "mode": run_data.mode,
            "model": ctx.model_name,
            "sandbox": run_data.sandbox,
        },
    )
    if run_data.sandbox:
        yield runtime_event_sse(
            "SANDBOX_STARTED",
            session_id=ctx.session_id,
            scope="sandbox",
            agent_id=ctx.agent_id,
            agent_name=ctx.agent_name,
            status="running",
            payload={
                "mode": run_data.mode,
                "model": ctx.model_name,
            },
        )
    yield _sse_event("SESSION", {
        "session_id": ctx.session_id,
    })
    _record_run_event(
        session_id=ctx.session_id,
        event_type="SESSION",
        persist=ctx.persist_run,
        agent_id=ctx.agent_id,
        agent_name=ctx.agent_name,
        status="running",
        title="Run started",
        payload={
            "mode": run_data.mode,
            "model": ctx.model_name,
            "sandbox": run_data.sandbox,
            "workspace": _workspace_trace_payload(ctx.guardrails),
        },
    )
    _record_run_event(
        session_id=ctx.session_id,
        event_type="READINESS_GATE",
        persist=ctx.persist_run,
        agent_id=ctx.agent_id,
        agent_name=ctx.agent_name,
        status="completed" if ctx.readiness.ready else "waiting",
        title="Readiness gate",
        content=ctx.readiness.reason,
        payload=ctx.readiness.model_dump(),
    )
    _record_run_event(
        session_id=ctx.session_id,
        event_type="INTENT_ROUTE",
        persist=ctx.persist_run,
        agent_id=ctx.agent_id,
        agent_name=ctx.agent_name,
        status=str(ctx.intent_route.mode),
        title=f"Intent route: {ctx.intent_route.mode} / {ctx.intent_route.executor.kind}",
        content=(
            f"architecture={ctx.intent_route.mode}; "
            f"executor={ctx.intent_route.executor.kind}; "
            f"runtimeSkills={len(ctx.used_skills)}; "
            f"tools={ctx.runtime_tool_scope.cache_key()}"
        ),
        payload=route_trace_payload(ctx.intent_route),
    )
    _record_workspace_event(
        session_id=ctx.session_id,
        persist=ctx.persist_run,
        scope="leader",
        agent_id=ctx.agent_id,
        agent_name=ctx.agent_name,
        guardrails=ctx.guardrails,
    )


async def emit_clarification_if_required(ctx: ChatRunContext) -> AsyncGenerator[str, None]:
    """旧兼容：输出非 AgentScope 恢复式 CLARIFICATION_REQUIRED。新反问走 AskUser。"""

    if ctx.readiness.ready or not ctx.readiness.clarification:
        return
    clarification = ctx.readiness.clarification
    if ctx.active_skill_run:
        ctx.active_skill_run = update_skill_run_status(
            ctx.active_skill_run,
            status="waiting_user",
            current_step=str(clarification.get("title") or "waiting_user"),
            pending_interaction_id=str(clarification.get("id") or ""),
        )
    _record_run_event(
        session_id=ctx.session_id,
        event_type="CLARIFICATION_REQUIRED",
        persist=ctx.persist_run,
        scope="leader",
        agent_id=ctx.agent_id,
        agent_name=ctx.agent_name,
        status="waiting",
        title=str(clarification.get("title") or "Clarification required"),
        payload=clarification,
    )
    if ctx.persist_run:
        _append_assistant_message(
            session_id=ctx.session_id,
            agent_id=ctx.agent_id,
            agent_name=ctx.agent_name,
            model_name=ctx.model_name,
            content="",
            error=None,
            skill_calls=ctx.skill_runs,
            clarification_requests=[clarification],
        )
    yield _sse_event("CLARIFICATION_REQUIRED", clarification)


async def emit_context_events(ctx: ChatRunContext) -> AsyncGenerator[str, None]:
    """输出 Team 初始状态和 Skill 注入上下文事件。"""

    if ctx.used_skill_details:
        for skill in ctx.used_skill_details:
            yield runtime_event_sse(
                "SKILL_STARTED",
                session_id=ctx.session_id,
                scope="skill",
                agent_id=ctx.agent_id,
                agent_name=ctx.agent_name,
                status="running",
                payload=skill,
            )
        yield _sse_event("SKILL_CONTEXT", {
            "skills": ctx.used_skill_details,
        })
        _record_run_event(
            session_id=ctx.session_id,
            event_type="SKILL_CONTEXT",
            persist=ctx.persist_run,
            agent_id=ctx.agent_id,
            agent_name=ctx.agent_name,
            status="running",
            title="Skills injected",
            payload={"skills": ctx.used_skill_details},
        )
