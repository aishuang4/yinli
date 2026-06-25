"""POST /send — 发送消息并通过 SSE 返回 Agent 事件流。"""

import asyncio
import json
import uuid
from typing import AsyncGenerator

from fastapi.responses import StreamingResponse
from fastapi import HTTPException
from pydantic import BaseModel

from agentscope.message import UserMsg

from src.config.runtime_settings import MODEL_PROVIDERS, get_effective_model_settings
from src.server.routes.agents import get_registered_agent
from src.server.routes.teams import get_registered_team
from src.server.routes.memory import (
    append_agent_memory_event,
)
from src.server.routes.skills import (
    build_skill_system_prompt_from_skills,
    list_skill_usage_details,
)

from src.server.routes.chat.helpers import _sse_event
from src.server.routes.chat.completion import (
    emit_failed_startup_run,
    finalize_failed_leader_run,
    finalize_successful_leader_run,
    handle_plan_approval_if_required,
)
from src.server.routes.chat.intent_router import (
    refine_team_route_with_model,
    route_chat_message,
    route_trace_payload,
)
from src.server.routes.chat.leader_stream import (
    LeaderStreamState,
    is_plan_runtime_tool_name,
    stream_leader_agent_reply,
)
from src.server.routes.chat.models import (
    MULTIMODAL_MODELS,
    ChatMessage,
    ChatModelCacheRun,
    ChatSkillRun,
)
from src.server.routes.chat.orchestration import (
    LeaderPreparationState,
    prepare_leader_after_team_workers,
)
from src.server.routes.chat.plans import (
    resolve_plan_approval,
)
from src.server.routes.chat.prompts import (
    _build_agent_memory_prompt,
    _build_clarification_system_prompt,
    _build_conversation_history_prompt,
    _build_leader_user_message,
    _build_runtime_context_prompt,
)
from src.server.routes.chat.router import router
from src.server.routes.chat.background_runs import (
    cancel_chat_background_run,
    get_chat_background_run,
    list_chat_background_runs,
    serialize_chat_background_run,
    start_chat_background_run,
    subscribe_chat_background_run,
)
from src.server.routes.chat.runtime import (
    _build_runtime_subagents,
    _build_subagent_context,
    _build_runtime_guardrails,
    _build_runtime_knowledge_tools,
    _build_runtime_mcp_servers,
    _build_user_content,
    _guardrail_cache_key,
)
from src.server.routes.chat.runtime_builder import (
    build_main_agent_runtime_spec,
    build_plan_runtime_context,
    build_routed_team_context,
    build_runtime_model_config,
    create_main_agent_from_spec,
    runtime_tool_scope_from_route,
    select_route_skills,
)
from src.server.routes.chat.skill_runs import (
    build_skill_run_system_prompt,
    get_active_session_skill_run,
    get_skill_for_run,
    start_or_resume_skill_run,
    update_skill_run_status,
)
from src.server.routes.chat.store import (
    _append_user_message,
    _make_session_title,
)
from src.server.routes.chat.team_state import _build_team_activities
from src.server.routes.chat.trace_recorder import (
    _make_fallback_trace_recorder,
    _record_run_event,
    _record_workspace_event,
    _trim_text,
    _workspace_trace_payload,
)
from src.storage.database import get_latest_user_chat_message


class PlanApprovalDecision(BaseModel):
    """计划审批决策。"""

    action: str


def _is_plan_runtime_tool_name(name: str) -> bool:
    """判断工具名是否为 Plan Runtime 内部工具。"""

    return is_plan_runtime_tool_name(name)


@router.post("/send")
async def send_message(data: ChatMessage) -> StreamingResponse:
    """
    发送消息并通过 SSE 返回 Agent 事件流

    调用流程：
    1. 获取或创建 Agent 实例（通过 factory）
    2. 构建 UserMsg 并调用 agent.reply_stream()
    3. 将 AgentScope 事件序列化为 SSE 格式推送到前端

    Args:
        data: 包含 session_id、message、agent_id 等的请求体

    Returns:
        StreamingResponse: SSE 事件流（text/event-stream）
    """

    session_id = data.session_id or uuid.uuid4().hex
    run_data = data.model_copy(update={"session_id": session_id})

    async def event_stream() -> AsyncGenerator[str, None]:
        """
        生成 SSE 事件流

        将 AgentScope 2.0 的 AgentEvent 转换为 SSE 格式：
          event: <EventType>
          data: <JSON>
        """
        session_id = run_data.session_id or uuid.uuid4().hex
        agent_id = run_data.agent_id or "default"
        agent_config = get_registered_agent(agent_id)
        agent_name = run_data.agent_name or (agent_config.name if agent_config else "Assistant")
        base_system_prompt = (
            run_data.system_prompt
            or (agent_config.system_prompt if agent_config else None)
            or "You are a helpful AI assistant."
        )
        runtime_context_prompt = _build_runtime_context_prompt()
        memory_system_prompt = _build_agent_memory_prompt(agent_id)
        history_system_prompt = _build_conversation_history_prompt(session_id)
        persist_run = not run_data.sandbox
        active_skill_run = get_active_session_skill_run(session_id, agent_id)
        active_skill = get_skill_for_run(active_skill_run)
        if active_skill_run and active_skill_run.get("status") == "waiting_user" and persist_run:
            active_skill_run = update_skill_run_status(
                active_skill_run,
                status="active",
                current_step="resumed_with_user_input",
                pending_interaction_id="",
            )
        skill_ids = agent_config.skills if agent_config and not run_data.skill_ids else run_data.skill_ids
        intent_route = route_chat_message(
            message=run_data.message,
            agent_config=agent_config,
            skill_ids=skill_ids,
            team_members=run_data.team_members,
            images=run_data.images,
        )
        runtime_model_settings = get_effective_model_settings()
        runtime_model_config = build_runtime_model_config(
            run_data=run_data,
            agent_config=agent_config,
            runtime_model_settings=runtime_model_settings,
        )
        intent_route = await refine_team_route_with_model(
            intent_route,
            message=run_data.message,
            agent_id=agent_id,
            team_name=run_data.team_name,
            team_members=run_data.team_members,
            model_name=runtime_model_config.model_name,
        )
        routed_team = build_routed_team_context(
            run_data=run_data,
            intent_route=intent_route,
            leader_agent_id=agent_id,
        )
        routed_team_members = routed_team.members
        routed_run_data = routed_team.run_data
        team_system_prompt = routed_team.system_prompt
        used_skills = select_route_skills(intent_route, active_skill)
        if not active_skill_run and used_skills and persist_run and not intent_route.require_clarification:
            active_skill_run = start_or_resume_skill_run(
                session_id=session_id,
                agent_id=agent_id,
                skill=used_skills[0],
                status="active",
                current_step="started",
            )
        skill_system_prompt = build_skill_system_prompt_from_skills(used_skills)
        skill_run_system_prompt = build_skill_run_system_prompt(active_skill_run)
        clarification_system_prompt = _build_clarification_system_prompt()
        used_skill_details = list_skill_usage_details(used_skills)
        skill_runs = [
            ChatSkillRun.model_validate({**skill_detail, "status": "running"})
            for skill_detail in used_skill_details
        ]
        model_name = runtime_model_config.model_name
        system_prompt = "\n\n".join(
            part
            for part in [
                base_system_prompt,
                runtime_context_prompt,
                intent_route.main_agent_instruction,
                memory_system_prompt,
                history_system_prompt,
                team_system_prompt,
                skill_run_system_prompt,
                skill_system_prompt,
                clarification_system_prompt,
            ]
            if part
        )
        guardrails = _build_runtime_guardrails(agent_config)
        runtime_tool_scope = runtime_tool_scope_from_route(intent_route)
        mcp_servers = _build_runtime_mcp_servers(agent_config)
        knowledge_tools = _build_runtime_knowledge_tools(agent_config)
        def record_subagent_event(payload: dict) -> None:
            _record_run_event(
                session_id=session_id,
                event_type=str(payload.get("eventType") or "SUBAGENT_CALL"),
                persist=persist_run,
                scope="leader",
                agent_id=agent_id,
                agent_name=agent_name,
                status=str(payload.get("status") or "running"),
                title=f"Subagent {payload.get('subagentName') or ''}".strip(),
                content=_trim_text(str(payload.get("task") or payload.get("outputPreview") or payload.get("error") or ""), 1200),
                payload=payload,
            )

        subagents = _build_runtime_subagents(
            agent_config,
            selected_subagent_ids=intent_route.selected_subagent_ids,
            on_warning=record_subagent_event,
        )
        subagent_context = _build_subagent_context(agent_config)
        assistant_text = ""
        assistant_thinking = ""
        assistant_error: str | None = None
        assistant_model_caches: list[ChatModelCacheRun] = []
        worker_outputs: list[dict[str, str]] = []
        team_activities = _build_team_activities(
            team_name=run_data.team_name,
            leader_agent_id=agent_id,
            leader_agent_name=agent_name,
            members=routed_team_members,
            user_message=run_data.message,
        )
        plan_build = await build_plan_runtime_context(
            intent_route=intent_route,
            agent_config=agent_config,
            session_id=session_id,
            agent_id=agent_id,
            team_id=run_data.team_id,
            title=_make_session_title(run_data.message),
            goal=run_data.message,
            team_members=routed_team_members,
            has_tools=bool(mcp_servers or knowledge_tools or subagents),
            has_skills=bool(skill_ids),
            system_prompt=system_prompt,
            model_config=runtime_model_config,
        )
        system_prompt = plan_build.system_prompt
        runtime_plan = plan_build.runtime_plan

        if persist_run:
            _append_user_message(
                session_id=session_id,
                agent_id=agent_id,
                agent_name=agent_name,
                team_id=run_data.team_id,
                team_name=run_data.team_name,
                model_name=model_name,
                content=run_data.message,
                images=run_data.images,
            )
            append_agent_memory_event(
                agent_id=agent_id,
                event_type="user_message",
                title=_make_session_title(run_data.message),
                content=run_data.message,
                session_id=session_id,
                metadata={
                    "agentName": agent_name,
                    "teamId": run_data.team_id,
                    "teamName": run_data.team_name,
                    "model": model_name,
                    "imageCount": len(run_data.images),
                    "sandbox": run_data.sandbox,
                },
            )
            _record_run_event(
                session_id=session_id,
                event_type="USER_MESSAGE",
                persist=persist_run,
                agent_id=agent_id,
                agent_name=agent_name,
                status="completed",
                title="User message",
                content=run_data.message,
                payload={
                    "teamId": run_data.team_id,
                    "teamName": run_data.team_name,
                    "model": model_name,
                    "imageCount": len(run_data.images),
                },
            )

        yield _sse_event("SESSION", {
            "session_id": session_id,
        })
        _record_run_event(
            session_id=session_id,
            event_type="SESSION",
            persist=persist_run,
            agent_id=agent_id,
            agent_name=agent_name,
            status="running",
            title="Run started",
            payload={
                "teamId": run_data.team_id,
                "teamName": run_data.team_name,
                "model": model_name,
                "sandbox": run_data.sandbox,
                "workspace": _workspace_trace_payload(guardrails),
            },
        )
        _record_run_event(
            session_id=session_id,
            event_type="INTENT_ROUTE",
            persist=persist_run,
            agent_id=agent_id,
            agent_name=agent_name,
            status=str(intent_route.mode),
            title=f"Intent route: {intent_route.mode}",
            content=(
                f"plan={intent_route.should_create_plan}; "
                f"skills={len(intent_route.selected_skill_ids)}; "
                f"subagents={len(intent_route.selected_subagent_ids)}; "
                f"tools={runtime_tool_scope.cache_key()}"
            ),
            payload=route_trace_payload(intent_route),
        )
        _record_workspace_event(
            session_id=session_id,
            persist=persist_run,
            scope="leader",
            agent_id=agent_id,
            agent_name=agent_name,
            guardrails=guardrails,
        )

        if intent_route.require_clarification and intent_route.clarification:
            clarification = intent_route.clarification
            if active_skill_run:
                active_skill_run = update_skill_run_status(
                    active_skill_run,
                    status="waiting_user",
                    current_step=str(clarification.get("title") or "waiting_user"),
                    pending_interaction_id=str(clarification.get("id") or ""),
                )
            _record_run_event(
                session_id=session_id,
                event_type="CLARIFICATION_REQUIRED",
                persist=persist_run,
                scope="leader",
                agent_id=agent_id,
                agent_name=agent_name,
                status="waiting",
                title=str(clarification.get("title") or "Clarification required"),
                payload=clarification,
            )
            yield _sse_event("CLARIFICATION_REQUIRED", clarification)
            return

        if team_activities:
            yield _sse_event("TEAM_ACTIVITY", {
                "activities": [item.model_dump(by_alias=True) for item in team_activities],
            })
            _record_run_event(
                session_id=session_id,
                event_type="TEAM_ACTIVITY",
                persist=persist_run,
                agent_id=agent_id,
                agent_name=agent_name,
                status="running",
                title="Team initialized",
                payload={"activities": [item.model_dump(by_alias=True) for item in team_activities]},
            )

        if used_skill_details:
            yield _sse_event("SKILL_CONTEXT", {
                "skills": used_skill_details,
            })
            _record_run_event(
                session_id=session_id,
                event_type="SKILL_CONTEXT",
                persist=persist_run,
                agent_id=agent_id,
                agent_name=agent_name,
                status="running",
                title="Skills injected",
                payload={"skills": used_skill_details},
            )
            for skill_detail in used_skill_details:
                if persist_run:
                    append_agent_memory_event(
                        agent_id=agent_id,
                        event_type="skill_use",
                        title=f"使用 Skill: {skill_detail.get('name', 'Skill')}",
                        content=str(skill_detail.get("content", "")),
                        session_id=session_id,
                        metadata={
                            "skillId": skill_detail.get("id"),
                            "skillName": skill_detail.get("name"),
                        },
                    )

        plan_approval_result: tuple[str, dict | None] = ("continue", runtime_plan)
        async for approval_event in handle_plan_approval_if_required(
            runtime_plan=runtime_plan,
            agent_config=agent_config,
            session_id=session_id,
            persist=persist_run,
            agent_id=agent_id,
            agent_name=agent_name,
            model_name=model_name,
            skill_runs=skill_runs,
            team_activities=team_activities,
            assistant_model_caches=assistant_model_caches,
        ):
            if isinstance(approval_event, tuple):
                plan_approval_result = approval_event
            else:
                yield approval_event
        runtime_plan = plan_approval_result[1]
        if plan_approval_result[0] != "continue":
            return

        if run_data.images and model_name not in MULTIMODAL_MODELS:
            assistant_error = f"{model_name} 不支持图片输入，请切换到 qwen3.7-plus。"
            async for failure_event in emit_failed_startup_run(
                session_id=session_id,
                persist=persist_run,
                agent_id=agent_id,
                agent_name=agent_name,
                model_name=model_name,
                assistant_error=assistant_error,
                skill_runs=skill_runs,
                team_activities=team_activities,
            ):
                yield failure_event
            return

        # 检查 API Key 是否配置
        provider = MODEL_PROVIDERS.get(runtime_model_settings.provider)
        if provider and provider.requires_api_key and not runtime_model_settings.api_key:
            assistant_error = f"{provider.name} API Key 未配置。请在系统设置中配置模型供应商和 API Key。"
            async for failure_event in emit_failed_startup_run(
                session_id=session_id,
                persist=persist_run,
                agent_id=agent_id,
                agent_name=agent_name,
                model_name=model_name,
                assistant_error=assistant_error,
                skill_runs=skill_runs,
                team_activities=team_activities,
            ):
                yield failure_event
            return

        # 获取或创建 Agent 实例
        try:
            leader_prep = LeaderPreparationState(system_prompt=system_prompt)
            async for worker_event in prepare_leader_after_team_workers(
                state=leader_prep,
                data=routed_run_data,
                session_id=session_id,
                team_activities=team_activities,
                model_name=model_name,
                leader_agent_id=agent_id,
                skill_runs=skill_runs,
                runtime_plan=runtime_plan,
            ):
                yield worker_event
            if leader_prep.waiting:
                return
            system_prompt = leader_prep.system_prompt
            worker_outputs = leader_prep.worker_outputs

            main_agent_spec = build_main_agent_runtime_spec(
                agent_id=agent_id,
                session_id=session_id,
                agent_name=agent_name,
                system_prompt=system_prompt,
                model_config=runtime_model_config,
                deep_thinking=run_data.deep_thinking,
                used_skills=used_skills,
                guardrails=guardrails,
                mcp_servers=mcp_servers,
                knowledge_tools=knowledge_tools,
                subagents=subagents,
                subagent_context=subagent_context,
                runtime_tool_scope=runtime_tool_scope,
                runtime_plan=runtime_plan,
                guardrail_cache_key=_guardrail_cache_key(guardrails),
            )
            agent = create_main_agent_from_spec(
                main_agent_spec,
                on_fallback_event=_make_fallback_trace_recorder(
                    session_id=session_id,
                    persist=persist_run,
                    scope="leader",
                    agent_id=agent_id,
                    agent_name=agent_name,
                ),
                on_subagent_event=record_subagent_event,
            )
        except Exception as e:
            assistant_error = f"Agent 创建失败: {str(e)}"
            async for failure_event in emit_failed_startup_run(
                session_id=session_id,
                persist=persist_run,
                agent_id=agent_id,
                agent_name=agent_name,
                model_name=model_name,
                assistant_error=assistant_error,
                skill_runs=skill_runs,
                team_activities=team_activities,
            ):
                yield failure_event
            return

        # 构建用户消息
        user_msg = UserMsg(
            name="user",
                content=_build_user_content(
                _build_leader_user_message(run_data.message, worker_outputs),
                run_data.images,
            ),
        )

        # 调用 AgentScope 真实 Agent 的 reply_stream
        try:
            leader_stream_state = LeaderStreamState(
                runtime_plan=runtime_plan,
                active_skill_run=active_skill_run,
            )
            async for leader_event in stream_leader_agent_reply(
                agent=agent,
                user_msg=user_msg,
                state=leader_stream_state,
                session_id=session_id,
                persist=persist_run,
                agent_id=agent_id,
                agent_name=agent_name,
                model_name=model_name,
                guardrails=guardrails,
                message=run_data.message,
                skill_runs=skill_runs,
                team_activities=team_activities,
            ):
                yield leader_event
            runtime_plan = leader_stream_state.runtime_plan
            active_skill_run = leader_stream_state.active_skill_run
            assistant_text = leader_stream_state.assistant_text
            assistant_thinking = leader_stream_state.assistant_thinking
            assistant_tool_calls = leader_stream_state.assistant_tool_calls
            assistant_model_caches = leader_stream_state.assistant_model_caches
            if leader_stream_state.stopped:
                return
            async for completion_event in finalize_successful_leader_run(
                session_id=session_id,
                persist=persist_run,
                agent_id=agent_id,
                agent_name=agent_name,
                model_name=model_name,
                user_message=run_data.message,
                assistant_text=assistant_text,
                assistant_thinking=assistant_thinking,
                assistant_tool_calls=assistant_tool_calls,
                assistant_model_caches=assistant_model_caches,
                skill_runs=skill_runs,
                active_skill_run=active_skill_run,
                team_activities=team_activities,
                runtime_plan=runtime_plan,
                team_id=run_data.team_id,
                team_name=run_data.team_name,
            ):
                yield completion_event

        except Exception as e:
            # Agent 执行过程中的异常处理
            assistant_error = f"Agent 执行异常: {str(e)}"
            async for completion_event in finalize_failed_leader_run(
                session_id=session_id,
                persist=persist_run,
                agent_id=agent_id,
                agent_name=agent_name,
                model_name=model_name,
                assistant_text=assistant_text,
                assistant_error=assistant_error,
                assistant_model_caches=assistant_model_caches,
                skill_runs=skill_runs,
                team_activities=team_activities,
                runtime_plan=runtime_plan,
            ):
                yield completion_event

    run = await start_chat_background_run(
        session_id=session_id,
        factory=event_stream,
        agent_id=run_data.agent_id or "default",
        agent_name=run_data.agent_name or "",
        team_id=run_data.team_id or "",
        team_name=run_data.team_name or "",
        task=run_data.message,
        mode="team" if run_data.team_id else "agent",
    )
    return StreamingResponse(
        subscribe_chat_background_run(run.id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/runs/{run_id}/stream")
async def stream_run(run_id: str) -> StreamingResponse:
    """重新订阅后台 Chat Run 事件。"""
    return StreamingResponse(
        subscribe_chat_background_run(run_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/runs")
async def list_runs() -> dict:
    """列出当前进程内后台 Chat Run。"""
    runs = await list_chat_background_runs()
    return {"runs": [serialize_chat_background_run(run) for run in runs]}


@router.get("/runs/{run_id}")
async def get_run(run_id: str) -> dict:
    """读取一个后台 Chat Run 的状态和事件缓存。"""
    run = await get_chat_background_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return {
        **serialize_chat_background_run(run),
        "events": run.events,
    }


@router.post("/runs/{run_id}/rerun")
async def rerun_run(run_id: str) -> dict:
    """基于历史 Run 的用户输入重新创建一个后台 Chat Run。"""

    source_run = await get_chat_background_run(run_id)
    if source_run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if source_run.status in {"running", "waiting", "cancelling"}:
        raise HTTPException(status_code=409, detail="Only terminal runs can be rerun")

    response = await send_message(_build_rerun_chat_message(source_run))
    new_run_id = await _read_started_run_id(response)
    new_run = await get_chat_background_run(new_run_id)
    if new_run is None:
        raise HTTPException(status_code=500, detail="Rerun was started but cannot be loaded")
    return serialize_chat_background_run(new_run)


@router.post("/plan-approvals/{approval_id}")
async def decide_plan_approval(approval_id: str, data: PlanApprovalDecision) -> dict:
    """处理 Plan Runtime 审批。"""
    action = "approve" if data.action == "approve" else "reject"
    if not resolve_plan_approval(approval_id, action):
        raise HTTPException(status_code=404, detail="Plan approval not found")
    return {"ok": True, "interactionId": approval_id, "action": action}


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str) -> dict:
    """取消一个仍在运行的后台 Chat Run。"""
    run = await cancel_chat_background_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return serialize_chat_background_run(run)


def _build_rerun_chat_message(source_run) -> ChatMessage:
    """从历史 Run 还原一条新的 Chat 请求，使用当前 Agent/Team 配置执行。"""

    original_user = get_latest_user_chat_message(source_run.session_id, source_run.task_text)
    message = source_run.task_text or str((original_user or {}).get("content") or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="Run task is empty and cannot be rerun")

    images = list((original_user or {}).get("images") or [])
    if source_run.mode == "team" and source_run.team_id:
        team = get_registered_team(source_run.team_id)
        if not team:
            raise HTTPException(status_code=404, detail="Team for rerun not found")
        leader = get_registered_agent(team.leader_agent_id)
        return ChatMessage(
            session_id=source_run.session_id,
            message=message,
            agent_id=team.leader_agent_id,
            agent_name=leader.name if leader else source_run.agent_name,
            team_id=team.id,
            team_name=team.name,
            team_members=[member.model_dump(by_alias=True) for member in team.members],
            images=images,
        )

    agent = get_registered_agent(source_run.agent_id) if source_run.agent_id else None
    if source_run.agent_id and not agent:
        raise HTTPException(status_code=404, detail="Agent for rerun not found")
    return ChatMessage(
        session_id=source_run.session_id,
        message=message,
        agent_id=source_run.agent_id or None,
        agent_name=agent.name if agent else source_run.agent_name,
        images=images,
    )


async def _read_started_run_id(response: StreamingResponse) -> str:
    """读取 send_message 返回流的首个 RUN 事件，拿到新建后台 Run ID。"""

    iterator = response.body_iterator
    try:
        async with asyncio.timeout(5):
            async for chunk in iterator:
                raw = chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk)
                event_type, data = _parse_sse_chunk(raw)
                if event_type == "RUN":
                    run_id = str(data.get("run_id") or data.get("runId") or "")
                    if run_id:
                        return run_id
                if event_type == "ERROR":
                    raise HTTPException(status_code=500, detail=str(data.get("message") or "Rerun failed"))
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail="Timed out waiting for rerun to start") from exc
    finally:
        close = getattr(iterator, "aclose", None)
        if callable(close):
            await close()
    raise HTTPException(status_code=500, detail="Rerun did not emit a RUN event")


def _parse_sse_chunk(raw: str) -> tuple[str, dict]:
    """解析单个 SSE 文本块。"""

    event_type = ""
    data_lines: list[str] = []
    for line in raw.splitlines():
        if line.startswith("event:"):
            event_type = line.removeprefix("event:").strip()
        elif line.startswith("data:"):
            data_lines.append(line.removeprefix("data:").strip())
    if not data_lines:
        return event_type, {}
    try:
        return event_type, json.loads("\n".join(data_lines))
    except json.JSONDecodeError:
        return event_type, {}
