"""POST /send — 发送消息并通过 SSE 返回 Agent 事件流。"""

import uuid
from typing import AsyncGenerator

from fastapi.responses import StreamingResponse

from agentscope.message import UserMsg

from src.agents.factory import AgentModelOptions, get_or_create_agent
from src.config.runtime_settings import MODEL_PROVIDERS, get_effective_model_settings
from src.server.routes.agents import get_registered_agent
from src.server.routes.memory import (
    append_agent_memory_event,
    maybe_summarize_agent_run,
)
from src.server.routes.skills import (
    build_skill_system_prompt_from_skills,
    list_skill_usage_details,
    select_relevant_skills,
)

from src.server.routes.chat.helpers import _set_skill_run_status, _sse_event
from src.server.routes.chat.hitl import _create_pending_interaction, _hitl_payload
from src.server.routes.chat.models import (
    HITL_EVENT_TYPES,
    MULTIMODAL_MODELS,
    ChatMessage,
    ChatModelCacheRun,
    ChatSkillRun,
)
from src.server.routes.chat.prompts import (
    _build_agent_memory_prompt,
    _build_agent_plan_prompt,
    _build_clarification_system_prompt,
    _build_conversation_history_prompt,
    _build_leader_user_message,
    _build_runtime_context_prompt,
    _build_team_system_prompt,
    _build_worker_outputs_system_prompt,
    _extract_clarification_request,
)
from src.server.routes.chat.router import router
from src.server.routes.chat.background_runs import (
    start_chat_background_run,
    subscribe_chat_background_run,
)
from src.server.routes.chat.runtime import (
    _build_runtime_guardrails,
    _build_runtime_knowledge_tools,
    _build_runtime_mcp_servers,
    _build_user_content,
    _permission_mode_for_guardrails,
)
from src.server.routes.chat.skill_runs import (
    build_skill_run_system_prompt,
    get_active_session_skill_run,
    get_skill_for_run,
    start_or_resume_skill_run,
    update_skill_run_status,
)
from src.server.routes.chat.store import (
    _append_assistant_message,
    _append_user_message,
    _make_session_title,
)
from src.server.routes.chat.team import _run_team_workers
from src.server.routes.chat.team_state import (
    _build_team_activities,
    _collect_worker_outputs,
    _find_team_activity,
    _finalize_team_activities,
    _mark_team_leader_waiting_for_clarification,
    _replace_team_activity,
    _set_team_activity_status,
    _update_worker_tool_calls,
)
from src.server.routes.chat.trace_recorder import (
    _attach_provider_cache_usage,
    _make_fallback_trace_recorder,
    _model_cache_from_event,
    _record_agent_scope_event,
    _record_run_event,
    _record_tool_execution_artifacts,
    _record_workspace_event,
    _trim_text,
    _workspace_trace_payload,
)


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
        plan_system_prompt = _build_agent_plan_prompt(agent_config)
        memory_system_prompt = _build_agent_memory_prompt(agent_id)
        history_system_prompt = _build_conversation_history_prompt(session_id)
        team_system_prompt = _build_team_system_prompt(run_data.team_name, run_data.team_members)
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
        used_skills = [active_skill] if active_skill else select_relevant_skills(skill_ids, run_data.message)
        if not active_skill_run and used_skills and persist_run:
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
        system_prompt = "\n\n".join(
            part
            for part in [
                base_system_prompt,
                runtime_context_prompt,
                plan_system_prompt,
                memory_system_prompt,
                history_system_prompt,
                team_system_prompt,
                skill_run_system_prompt,
                skill_system_prompt,
                clarification_system_prompt,
            ]
            if part
        )
        runtime_model_settings = get_effective_model_settings()
        model_config_data = agent_config.model_config_data if agent_config else None
        model_name = (
            run_data.model
            or (model_config_data.primary if model_config_data else None)
            or runtime_model_settings.default_model
        )
        fallback_names = model_config_data.fallback if model_config_data else runtime_model_settings.fallback_models
        model_options = AgentModelOptions(
            temperature=model_config_data.temperature if model_config_data else None,
            top_p=model_config_data.top_p if model_config_data else None,
            max_tokens=model_config_data.max_tokens if model_config_data else None,
            max_react_iters=model_config_data.max_react_iters if model_config_data else 40,
        )
        guardrails = _build_runtime_guardrails(agent_config)
        mcp_servers = _build_runtime_mcp_servers(agent_config)
        knowledge_tools = _build_runtime_knowledge_tools(agent_config)
        assistant_text = ""
        assistant_thinking = ""
        assistant_error: str | None = None
        assistant_model_caches: list[ChatModelCacheRun] = []
        worker_outputs: list[dict[str, str]] = []
        team_activities = _build_team_activities(
            team_name=run_data.team_name,
            leader_agent_id=agent_id,
            leader_agent_name=agent_name,
            members=run_data.team_members,
            user_message=run_data.message,
        )

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
        _record_workspace_event(
            session_id=session_id,
            persist=persist_run,
            scope="leader",
            agent_id=agent_id,
            agent_name=agent_name,
            guardrails=guardrails,
        )

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

        if run_data.images and model_name not in MULTIMODAL_MODELS:
            assistant_error = f"{model_name} 不支持图片输入，请切换到 qwen3.7-plus。"
            failed_skill_runs = _set_skill_run_status(skill_runs, "failed")
            if persist_run:
                _append_assistant_message(
                    session_id=session_id,
                    agent_id=agent_id,
                    agent_name=agent_name,
                    model_name=model_name,
                    content="",
                    error=assistant_error,
                    skill_calls=failed_skill_runs,
                    team_activities=_set_team_activity_status(team_activities, "failed"),
                )
            if team_activities:
                yield _sse_event("TEAM_ACTIVITY", {
                    "activities": [
                        item.model_dump(by_alias=True)
                        for item in _set_team_activity_status(team_activities, "failed")
                    ],
                })
            if failed_skill_runs:
                yield _sse_event("SKILL_END", {
                    "skills": [item.model_dump(by_alias=True) for item in failed_skill_runs],
                    "status": "failed",
                })
            yield _sse_event("ERROR", {
                "message": assistant_error,
            })
            return

        # 检查 API Key 是否配置
        provider = MODEL_PROVIDERS.get(runtime_model_settings.provider)
        if provider and provider.requires_api_key and not runtime_model_settings.api_key:
            assistant_error = f"{provider.name} API Key 未配置。请在系统设置中配置模型供应商和 API Key。"
            failed_skill_runs = _set_skill_run_status(skill_runs, "failed")
            if persist_run:
                _append_assistant_message(
                    session_id=session_id,
                    agent_id=agent_id,
                    agent_name=agent_name,
                    model_name=model_name,
                    content="",
                    error=assistant_error,
                    skill_calls=failed_skill_runs,
                    team_activities=_set_team_activity_status(team_activities, "failed"),
                )
            if team_activities:
                yield _sse_event("TEAM_ACTIVITY", {
                    "activities": [
                        item.model_dump(by_alias=True)
                        for item in _set_team_activity_status(team_activities, "failed")
                    ],
                })
            if failed_skill_runs:
                yield _sse_event("SKILL_END", {
                    "skills": [item.model_dump(by_alias=True) for item in failed_skill_runs],
                    "status": "failed",
                })
            yield _sse_event("ERROR", {
                "message": assistant_error,
            })
            return

        # 获取或创建 Agent 实例
        try:
            if team_activities:
                async for worker_event in _run_team_workers(
                    data=run_data,
                    session_id=session_id,
                    team_activities=team_activities,
                    model_name=model_name,
                    leader_system_prompt=system_prompt,
                    skill_runs=skill_runs,
                ):
                    yield worker_event
                if any(activity.status == "waiting" for activity in team_activities):
                    return
            worker_outputs = _collect_worker_outputs(team_activities)
            if worker_outputs:
                system_prompt = "\n\n".join(
                    part
                    for part in [
                        system_prompt,
                        _build_worker_outputs_system_prompt(worker_outputs),
                    ]
                    if part
                )
                leader_activity = _find_team_activity(team_activities, agent_id)
                if leader_activity:
                    _replace_team_activity(
                        team_activities,
                        leader_activity.model_copy(update={
                            "status": "running",
                            "detail": f"Worker 已完成，Leader 正在基于 {len(worker_outputs)} 个 Worker 输出生成最终回复。",
                            "leader_context_count": len(worker_outputs),
                        }),
                    )
                    yield _sse_event("TEAM_ACTIVITY", {
                        "activities": [item.model_dump(by_alias=True) for item in team_activities],
                    })

            skill_cache_key = ",".join(sorted(skill.id for skill in used_skills))
            fallback_cache_key = ",".join(fallback_names)
            mcp_cache_key = ",".join(
                f"{server.name}:{','.join(server.enable_tools)}"
                for server in mcp_servers
            )
            knowledge_cache_key = ",".join(tool.id for tool in knowledge_tools)
            guardrail_cache_key = (
                f"scope-{guardrails.filesystem_scope}:"
                f"net-{guardrails.network_enabled}:"
                f"hitl-{guardrails.human_approval}:"
                f"tool-permission-{guardrails.tool_permission_mode}:"
                f"tool-profile-{guardrails.tool_permission_profile}:"
                f"exec-{guardrails.execution_backend}:"
                f"docker-{guardrails.docker_image}:"
                f"persist-sandbox-{guardrails.persistent_sandbox}:"
                f"timeout-{guardrails.timeout_seconds}"
            )
            runtime_agent_id = (
                f"{agent_id}:{session_id}:{model_name}:thinking-{run_data.deep_thinking}:"
                f"fallbacks-{fallback_cache_key}:"
                f"skills-{skill_cache_key}:mcp-{mcp_cache_key}:knowledge-{knowledge_cache_key}:"
                f"guardrails-{guardrail_cache_key}"
            )
            agent = get_or_create_agent(
                agent_id=runtime_agent_id,
                name=agent_name,
                system_prompt=system_prompt,
                model_name=model_name,
                fallback_names=fallback_names,
                thinking_enable=run_data.deep_thinking,
                permission_mode=_permission_mode_for_guardrails(guardrails),
                model_options=model_options,
                guardrails=guardrails,
                mcp_servers=mcp_servers,
                knowledge_tools=knowledge_tools,
                on_fallback_event=_make_fallback_trace_recorder(
                    session_id=session_id,
                    persist=persist_run,
                    scope="leader",
                    agent_id=agent_id,
                    agent_name=agent_name,
                ),
            )
        except Exception as e:
            assistant_error = f"Agent 创建失败: {str(e)}"
            failed_skill_runs = _set_skill_run_status(skill_runs, "failed")
            if persist_run:
                _append_assistant_message(
                    session_id=session_id,
                    agent_id=agent_id,
                    agent_name=agent_name,
                    model_name=model_name,
                    content="",
                    error=assistant_error,
                    skill_calls=failed_skill_runs,
                    team_activities=_set_team_activity_status(team_activities, "failed"),
                )
            if team_activities:
                yield _sse_event("TEAM_ACTIVITY", {
                    "activities": [
                        item.model_dump(by_alias=True)
                        for item in _set_team_activity_status(team_activities, "failed")
                    ],
                })
            if failed_skill_runs:
                yield _sse_event("SKILL_END", {
                    "skills": [item.model_dump(by_alias=True) for item in failed_skill_runs],
                    "status": "failed",
                })
            yield _sse_event("ERROR", {
                "message": assistant_error,
            })
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
            assistant_tool_calls: list = []
            async for event in agent.reply_stream(user_msg):
                # 将 Pydantic 事件模型序列化为 dict
                event_data = event.model_dump(mode="json")

                # 提取事件类型（EventType 枚举值）
                event_type = event_data.pop("type", "UNKNOWN")
                _attach_provider_cache_usage(event_type, event_data)
                if event_type == "MODEL_CALL_END":
                    cache_entry = _model_cache_from_event(event_data, model_name)
                    if cache_entry:
                        assistant_model_caches.append(cache_entry)
                _record_agent_scope_event(
                    session_id=session_id,
                    event_type=event_type,
                    event_data=event_data,
                    persist=persist_run,
                    scope="leader",
                    agent_id=agent_id,
                    agent_name=agent_name,
                )
                next_tool_calls = _update_worker_tool_calls(
                    assistant_tool_calls,
                    event_type,
                    event_data,
                )
                if next_tool_calls is not assistant_tool_calls:
                    assistant_tool_calls = next_tool_calls
                _record_tool_execution_artifacts(
                    session_id=session_id,
                    event_type=event_type,
                    event_data=event_data,
                    persist=persist_run,
                    scope="leader",
                    agent_id=agent_id,
                    agent_name=agent_name,
                    team_activity_id=None,
                    guardrails=guardrails,
                    tool_calls=assistant_tool_calls,
                )
                if event_type in HITL_EVENT_TYPES:
                    if active_skill_run and persist_run:
                        active_skill_run = update_skill_run_status(
                            active_skill_run,
                            status="waiting_user",
                            current_step="waiting_hitl_resume",
                        )
                    interaction = _create_pending_interaction(
                        interaction_type=event_type,
                        agent=agent,
                        session_id=session_id,
                        agent_id=agent_id,
                        agent_name=agent_name,
                        model_name=model_name,
                        reply_id=str(event_data.get("reply_id") or ""),
                        tool_calls=list(event_data.get("tool_calls") or []),
                        message=run_data.message,
                        assistant_text=assistant_text,
                        assistant_tool_calls=assistant_tool_calls,
                        skill_runs=skill_runs,
                        team_activities=team_activities,
                        persist=persist_run,
                    )
                    yield _sse_event("HITL_REQUIRED", _hitl_payload(interaction))
                    return
                if event_type == "TEXT_BLOCK_DELTA":
                    assistant_text += str(event_data.get("delta", ""))
                elif event_type == "THINKING_BLOCK_DELTA":
                    assistant_thinking += str(event_data.get("delta", ""))

                if event_type == "REPLY_END" and skill_runs:
                    completed_skill_runs = _set_skill_run_status(skill_runs, "completed")
                    yield _sse_event("SKILL_END", {
                        "skills": [item.model_dump(by_alias=True) for item in completed_skill_runs],
                        "status": "completed",
                    })

                # 发送 SSE 事件
                yield _sse_event(event_type, event_data)

            clarification = _extract_clarification_request(assistant_text) or _extract_clarification_request(assistant_thinking)
            if clarification:
                if active_skill_run:
                    active_skill_run = update_skill_run_status(
                        active_skill_run,
                        status="waiting_user",
                        current_step=str(clarification.get("title") or "waiting_user"),
                        pending_interaction_id=str(clarification.get("id") or ""),
                    )
                _mark_team_leader_waiting_for_clarification(team_activities, agent_id)
                if team_activities:
                    yield _sse_event("TEAM_ACTIVITY", {
                        "activities": [item.model_dump(by_alias=True) for item in team_activities],
                    })
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

            completed_skill_runs = _set_skill_run_status(skill_runs, "completed")
            if active_skill_run:
                active_skill_run = update_skill_run_status(
                    active_skill_run,
                    status="completed",
                    current_step="completed",
                    pending_interaction_id="",
                )
            completed_team_activities = _finalize_team_activities(
                team_activities,
                leader_agent_id=agent_id,
                leader_output=assistant_text,
            )

            if persist_run:
                _append_assistant_message(
                    session_id=session_id,
                    agent_id=agent_id,
                    agent_name=agent_name,
                    model_name=model_name,
                    content=assistant_text,
                    error=assistant_error,
                    thinking=assistant_thinking,
                    skill_calls=completed_skill_runs,
                    tool_calls=assistant_tool_calls,
                    team_activities=completed_team_activities,
                    model_caches=assistant_model_caches,
                )
                _record_run_event(
                    session_id=session_id,
                    event_type="ASSISTANT_MESSAGE",
                    persist=persist_run,
                    scope="leader",
                    agent_id=agent_id,
                    agent_name=agent_name,
                    status="completed" if not assistant_error else "failed",
                    title="Assistant response",
                    content=_trim_text(assistant_text, 1200),
                    payload={
                        "model": model_name,
                        "toolCallCount": len(assistant_tool_calls),
                        "skillIds": [item.id for item in completed_skill_runs],
                    },
                )
            if team_activities:
                yield _sse_event("TEAM_ACTIVITY", {
                    "activities": [
                        item.model_dump(by_alias=True)
                        for item in completed_team_activities
                    ],
                })
            if persist_run:
                append_agent_memory_event(
                    agent_id=agent_id,
                    event_type="assistant_message",
                    title="Assistant 回复",
                    content=assistant_text,
                    session_id=session_id,
                    metadata={
                        "agentName": agent_name,
                        "model": model_name,
                        "skillIds": [item.id for item in completed_skill_runs],
                    },
                )
                memory_facts = await maybe_summarize_agent_run(
                    agent_id=agent_id,
                    agent_name=agent_name,
                    session_id=session_id,
                    user_message=run_data.message,
                    assistant_text=assistant_text,
                    metadata={
                        "agentName": agent_name,
                        "teamId": run_data.team_id,
                        "teamName": run_data.team_name,
                        "model": model_name,
                        "skillIds": [item.id for item in completed_skill_runs],
                    },
                )
                if memory_facts:
                    yield _sse_event("MEMORY_SUMMARY", {
                        "agentId": agent_id,
                        "agentName": agent_name,
                        "facts": [fact.model_dump(mode="json") for fact in memory_facts],
                    })

        except Exception as e:
            # Agent 执行过程中的异常处理
            assistant_error = f"Agent 执行异常: {str(e)}"
            failed_skill_runs = _set_skill_run_status(skill_runs, "failed")
            _record_run_event(
                session_id=session_id,
                event_type="ERROR",
                persist=persist_run,
                scope="leader",
                agent_id=agent_id,
                agent_name=agent_name,
                status="failed",
                title="Agent execution failed",
                content=assistant_error,
                payload={"model": model_name},
            )
            if persist_run:
                _append_assistant_message(
                    session_id=session_id,
                    agent_id=agent_id,
                    agent_name=agent_name,
                    model_name=model_name,
                    content=assistant_text,
                    error=assistant_error,
                    skill_calls=failed_skill_runs,
                    team_activities=_set_team_activity_status(team_activities, "failed"),
                    model_caches=assistant_model_caches,
                )
                append_agent_memory_event(
                    agent_id=agent_id,
                    event_type="assistant_message",
                    title="Assistant 异常回复",
                    content=assistant_text or assistant_error,
                    session_id=session_id,
                    metadata={
                        "agentName": agent_name,
                        "model": model_name,
                        "error": assistant_error,
                        "skillIds": [item.id for item in failed_skill_runs],
                    },
                )
            if failed_skill_runs:
                yield _sse_event("SKILL_END", {
                    "skills": [item.model_dump(by_alias=True) for item in failed_skill_runs],
                    "status": "failed",
                })
            if team_activities:
                yield _sse_event("TEAM_ACTIVITY", {
                    "activities": [
                        item.model_dump(by_alias=True)
                        for item in _set_team_activity_status(team_activities, "failed")
                    ],
                })
            yield _sse_event("ERROR", {
                "message": assistant_error,
            })

    run = await start_chat_background_run(
        session_id=session_id,
        factory=event_stream,
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
