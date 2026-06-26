"""POST /resume — 人工确认或外部执行结果后恢复 Agent / Team 运行。"""

from typing import AsyncGenerator

from fastapi.responses import StreamingResponse

from agentscope.message import UserMsg

from src.agents.factory import AgentModelOptions, get_or_create_agent
from src.config.runtime_settings import get_effective_model_settings
from src.server.routes.agents import get_registered_agent

from src.server.routes.chat.helpers import _set_skill_run_status, _sse_event, _trim_text
from src.server.routes.chat.hitl import (
    _build_resume_event,
    _clear_pending_interaction,
    _create_pending_interaction,
    _hitl_payload,
)
from src.server.routes.chat.models import (
    PENDING_AGENTS,
    PENDING_INTERACTIONS,
    HITL_EVENT_TYPES,
    ChatMessage,
    ChatModelCacheRun,
    ChatResumeRequest,
    ChatTeamMemberPayload,
    ChatToolRun,
)
from src.server.routes.chat.prompts import (
    _build_leader_user_message,
    _build_worker_outputs_system_prompt,
)
from src.server.routes.chat.router import router
from src.server.routes.chat.background_runs import relay_chat_background_run_events
from src.server.routes.chat.runtime import (
    _build_runtime_guardrails,
    _build_runtime_knowledge_tools,
    _build_runtime_mcp_servers,
    _build_user_content,
    _guardrail_cache_key,
    _permission_mode_for_guardrails,
)
from src.server.routes.chat.runtime_events import (
    runtime_event_from_agentscope_sse,
    runtime_event_sse,
)
from src.server.routes.chat.skill_runs import (
    get_active_session_skill_run,
    get_skill_run_for_pending_interaction,
    update_skill_run_status,
)
from src.server.routes.chat.store import _append_assistant_message
from src.server.routes.chat.team import _run_team_workers
from src.server.routes.chat.team_state import (
    _collect_worker_outputs,
    _find_team_activity,
    _finalize_team_activities,
    _pending_tool_calls_to_runs,
    _replace_team_activity,
    _set_team_activity_status,
    _update_worker_tool_calls,
)
from src.server.routes.chat.trace_recorder import (
    _attach_provider_cache_usage,
    build_tool_artifact_runtime_event,
    _model_cache_from_event,
    _record_agent_scope_event,
    _record_permission_decision,
    _record_run_event,
    _record_tool_execution_artifacts,
)


@router.post("/resume")
async def resume_interaction(data: ChatResumeRequest) -> StreamingResponse:
    """Resume a paused Agent run after user confirmation or external result."""

    async def event_stream() -> AsyncGenerator[str, None]:
        interaction = PENDING_INTERACTIONS.get(data.interaction_id)
        agent = PENDING_AGENTS.get(data.interaction_id)
        if interaction is None or agent is None:
            stale_skill_run = get_skill_run_for_pending_interaction(data.interaction_id)
            if stale_skill_run:
                update_skill_run_status(
                    stale_skill_run,
                    status="completed",
                    current_step="stale_after_backend_restart",
                    pending_interaction_id="",
                )
            yield _sse_event("ERROR", {
                "message": "待处理交互不存在或后端已重启，无法恢复原 AgentScope ReAct。请重新发送上一条需求继续。",
                "recoverable": True,
                "reason": "missing_pending_interaction",
            })
            return

        try:
            resume_event = _build_resume_event(interaction, data)
        except Exception as exc:
            yield _sse_event("ERROR", {"message": f"恢复事件构建失败: {exc}"})
            return

        assistant_text = interaction.assistant_text
        assistant_thinking = ""
        assistant_error: str | None = None
        assistant_model_caches: list[ChatModelCacheRun] = []
        skill_runs = interaction.skill_runs
        team_activities = interaction.team_activities
        activity = _find_team_activity(team_activities, interaction.agent_id)

        def _mark_persistent_skill_run(
            agent_id: str,
            *,
            status: str,
            current_step: str,
            pending_interaction_id: str = "",
        ) -> None:
            run = get_active_session_skill_run(interaction.session_id, agent_id)
            if run:
                update_skill_run_status(
                    run,
                    status=status,
                    current_step=current_step,
                    pending_interaction_id=pending_interaction_id,
                )

        _record_run_event(
            session_id=interaction.session_id,
            event_type="HITL_RESOLVED",
            persist=True,
            scope=interaction.scope,
            agent_id=interaction.agent_id,
            agent_name=interaction.agent_name,
            team_activity_id=activity.id if activity else None,
            status="completed" if data.action == "approve" else "rejected",
            title=f"{interaction.scope.title()} human approval {data.action}",
            payload={
                "interactionId": interaction.id,
                "interactionType": interaction.type,
                "action": data.action,
                "externalResultCount": len(data.external_results),
            },
        )
        yield runtime_event_sse(
            "HITL_RESOLVED",
            session_id=interaction.session_id,
            run_id=interaction.run_id,
            scope=interaction.scope,
            agent_id=interaction.agent_id,
            agent_name=interaction.agent_name,
            team_activity_id=activity.id if activity else None,
            status="completed" if data.action == "approve" else "failed",
            payload={
                "interactionId": interaction.id,
                "interactionType": interaction.type,
                "action": data.action,
                "externalResultCount": len(data.external_results),
            },
        )
        yield runtime_event_sse(
            "RUN_RESUMED",
            session_id=interaction.session_id,
            run_id=interaction.run_id,
            scope="run",
            agent_id=interaction.agent_id,
            agent_name=interaction.agent_name,
            status="running",
            payload={
                "interactionId": interaction.id,
                "interactionType": interaction.type,
                "action": data.action,
            },
        )
        yield runtime_event_sse(
            "AGENT_RESUMED",
            session_id=interaction.session_id,
            run_id=interaction.run_id,
            scope=interaction.scope,
            agent_id=interaction.agent_id,
            agent_name=interaction.agent_name,
            team_activity_id=activity.id if activity else None,
            status="running",
            payload={
                "interactionId": interaction.id,
                "interactionType": interaction.type,
                "action": data.action,
            },
        )

        try:
            if interaction.scope == "worker":
                chat_data = ChatMessage.model_validate(interaction.chat_data or {})
                worker_member = ChatTeamMemberPayload.model_validate(interaction.worker_member or {})
                worker_text = interaction.worker_text
                worker_thinking = interaction.worker_thinking
                worker_tool_calls = _pending_tool_calls_to_runs(
                    interaction.tool_calls,
                    interaction.worker_tool_calls,
                )
                worker_guardrails = _build_runtime_guardrails(get_registered_agent(worker_member.agent_id))

                activity = _find_team_activity(team_activities, worker_member.agent_id)
                if activity:
                    _replace_team_activity(
                        team_activities,
                        activity.model_copy(update={
                            "status": "running",
                            "detail": "人工确认已处理，继续 Worker ReAct。",
                            "output": worker_text,
                            "thinking": worker_thinking,
                            "tool_calls": worker_tool_calls,
                        }),
                    )
                    yield _sse_event("TEAM_ACTIVITY", {
                        "activities": [item.model_dump(by_alias=True) for item in team_activities],
                    })

                async for event in agent.reply_stream(resume_event):
                    event_data = event.model_dump(mode="json")
                    event_type = event_data.pop("type", "UNKNOWN")
                    _attach_provider_cache_usage(event_type, event_data)
                    if event_type == "MODEL_CALL_END":
                        cache_entry = _model_cache_from_event(event_data, interaction.model_name)
                        if cache_entry:
                            assistant_model_caches.append(cache_entry)
                    _record_agent_scope_event(
                        session_id=interaction.session_id,
                        event_type=event_type,
                        event_data=event_data,
                        persist=True,
                        scope="worker",
                        agent_id=worker_member.agent_id,
                        agent_name=interaction.agent_name,
                        team_activity_id=activity.id if activity else None,
                    )
                    _record_permission_decision(
                        session_id=interaction.session_id,
                        event_type=event_type,
                        event_data=event_data,
                        persist=True,
                        scope="worker",
                        agent_id=worker_member.agent_id,
                        agent_name=interaction.agent_name,
                        team_activity_id=activity.id if activity else None,
                        guardrails=worker_guardrails,
                    )
                    runtime_event = runtime_event_from_agentscope_sse(
                        event_type,
                        session_id=interaction.session_id,
                        scope="worker",
                        agent_id=worker_member.agent_id,
                        agent_name=interaction.agent_name,
                        team_activity_id=activity.id if activity else None,
                        event_data=event_data,
                    )
                    if runtime_event:
                        yield runtime_event
                    if event_type in HITL_EVENT_TYPES:
                        _mark_persistent_skill_run(
                            worker_member.agent_id,
                            status="waiting_user",
                            current_step="waiting_hitl_resume",
                        )
                        next_interaction = _create_pending_interaction(
                            interaction_type=event_type,
                            agent=agent,
                            session_id=interaction.session_id,
                            agent_id=worker_member.agent_id,
                            agent_name=interaction.agent_name,
                            model_name=interaction.model_name,
                            reply_id=str(event_data.get("reply_id") or ""),
                            tool_calls=list(event_data.get("tool_calls") or []),
                            message=interaction.message,
                            assistant_text="",
                            skill_runs=skill_runs,
                            team_activities=team_activities,
                            scope="worker",
                            chat_data=interaction.chat_data,
                            leader_system_prompt=interaction.leader_system_prompt,
                            worker_member=interaction.worker_member,
                            worker_text=worker_text,
                            worker_thinking=worker_thinking,
                            worker_tool_calls=worker_tool_calls,
                        )
                        activity = _find_team_activity(team_activities, worker_member.agent_id)
                        if activity:
                            _replace_team_activity(
                                team_activities,
                                activity.model_copy(update={
                                    "status": "waiting",
                                    "detail": "等待人工确认后继续 Worker ReAct。",
                                    "output": worker_text,
                                    "thinking": worker_thinking,
                                    "tool_calls": worker_tool_calls,
                                }),
                            )
                            yield _sse_event("TEAM_ACTIVITY", {
                                "activities": [item.model_dump(by_alias=True) for item in team_activities],
                            })
                        _clear_pending_interaction(interaction.id)
                        yield runtime_event_sse(
                            "TEAM_WORKER_WAITING_HUMAN",
                            session_id=interaction.session_id,
                            run_id=interaction.run_id,
                            scope="worker",
                            source="runtime",
                            source_event_type=event_type,
                            agent_id=worker_member.agent_id,
                            agent_name=interaction.agent_name,
                            team_activity_id=activity.id if activity else None,
                            status="waiting",
                            reason="hitl_required",
                            payload={"interactionId": next_interaction.id, "interactionType": event_type},
                        )
                        yield runtime_event_sse(
                            "AGENT_WAITING_HUMAN",
                            session_id=interaction.session_id,
                            run_id=interaction.run_id,
                            scope="worker",
                            source="runtime",
                            source_event_type=event_type,
                            agent_id=worker_member.agent_id,
                            agent_name=interaction.agent_name,
                            team_activity_id=activity.id if activity else None,
                            status="waiting",
                            reason="hitl_required",
                            payload={"interactionId": next_interaction.id, "interactionType": event_type},
                        )
                        yield _sse_event("HITL_REQUIRED", _hitl_payload(next_interaction))
                        return

                    if event_type == "TEXT_BLOCK_DELTA":
                        worker_text += str(event_data.get("delta", ""))
                    elif event_type == "THINKING_BLOCK_DELTA":
                        worker_thinking += str(event_data.get("delta", ""))

                    next_tool_calls = _update_worker_tool_calls(
                        worker_tool_calls,
                        event_type,
                        event_data,
                    )
                    if next_tool_calls is not worker_tool_calls:
                        worker_tool_calls = next_tool_calls
                    activity = _find_team_activity(team_activities, worker_member.agent_id)
                    _record_tool_execution_artifacts(
                        session_id=interaction.session_id,
                        event_type=event_type,
                        event_data=event_data,
                        persist=True,
                        scope="worker",
                        agent_id=worker_member.agent_id,
                        agent_name=interaction.agent_name,
                        team_activity_id=activity.id if activity else None,
                        guardrails=worker_guardrails,
                        tool_calls=worker_tool_calls,
                    )
                    artifact_event = build_tool_artifact_runtime_event(
                        event_type=event_type,
                        event_data=event_data,
                        tool_calls=worker_tool_calls,
                    )
                    if artifact_event:
                        artifact_type, artifact_payload = artifact_event
                        yield runtime_event_sse(
                            artifact_type,
                            session_id=interaction.session_id,
                            run_id=interaction.run_id,
                            scope="artifact",
                            source="runtime",
                            source_event_type=event_type,
                            agent_id=worker_member.agent_id,
                            agent_name=interaction.agent_name,
                            team_activity_id=activity.id if activity else None,
                            status=str(artifact_payload.get("status") or "completed"),
                            payload=artifact_payload,
                        )

                    activity = _find_team_activity(team_activities, worker_member.agent_id)
                    if activity:
                        _replace_team_activity(
                            team_activities,
                            activity.model_copy(update={
                                "status": "running",
                                "output": worker_text,
                                "thinking": worker_thinking,
                                "tool_calls": worker_tool_calls,
                                "detail": _trim_text(worker_text, 120) or activity.detail,
                            }),
                        )
                        yield _sse_event("TEAM_MEMBER_EVENT", {
                            "activityId": activity.id,
                            "agentId": worker_member.agent_id,
                            "agentName": interaction.agent_name,
                            "role": "worker",
                            "eventType": event_type,
                            "event": event_data,
                        })

                activity = _find_team_activity(team_activities, worker_member.agent_id)
                _mark_persistent_skill_run(
                    worker_member.agent_id,
                    status="completed",
                    current_step="completed",
                )
                if activity:
                    _replace_team_activity(
                        team_activities,
                        activity.model_copy(update={
                            "status": "completed",
                            "output": worker_text,
                            "thinking": worker_thinking,
                            "tool_calls": worker_tool_calls,
                            "detail": _trim_text(worker_text, 160) or "Worker 已完成本轮 ReAct 循环。",
                        }),
                    )
                    yield _sse_event("TEAM_ACTIVITY", {
                        "activities": [item.model_dump(by_alias=True) for item in team_activities],
                    })
                _record_run_event(
                    session_id=interaction.session_id,
                    event_type="WORKER_COMPLETED",
                    persist=True,
                    scope="worker",
                    agent_id=worker_member.agent_id,
                    agent_name=interaction.agent_name,
                    team_activity_id=activity.id if activity else None,
                    status="completed",
                    title=f"Worker completed: {interaction.agent_name}",
                    content=_trim_text(worker_text, 1200),
                    payload={
                        "toolCallCount": len(worker_tool_calls),
                        "resumedFromHitl": True,
                    },
                )
                yield runtime_event_sse(
                    "TEAM_WORKER_COMPLETED",
                    session_id=interaction.session_id,
                    run_id=interaction.run_id,
                    scope="worker",
                    agent_id=worker_member.agent_id,
                    agent_name=interaction.agent_name,
                    team_activity_id=activity.id if activity else None,
                    status="completed",
                    payload={
                        "toolCallCount": len(worker_tool_calls),
                        "outputPreview": _trim_text(worker_text, 240),
                        "resumedFromHitl": True,
                    },
                )

                _clear_pending_interaction(interaction.id)
                async for worker_event in _run_team_workers(
                    data=chat_data,
                    session_id=interaction.session_id,
                    team_activities=team_activities,
                    model_name=interaction.model_name,
                    leader_system_prompt=interaction.leader_system_prompt,
                    skill_runs=skill_runs,
                ):
                    yield worker_event
                if any(activity.status == "waiting" for activity in team_activities):
                    return

                worker_outputs = _collect_worker_outputs(team_activities)
                leader_system_prompt = "\n\n".join(
                    part
                    for part in [
                        interaction.leader_system_prompt,
                        _build_worker_outputs_system_prompt(worker_outputs),
                    ]
                    if part
                )
                leader_agent_id = chat_data.agent_id or interaction.agent_id
                leader_agent_name = chat_data.agent_name or "Leader"
                leader_config = get_registered_agent(leader_agent_id)
                runtime_model_settings = get_effective_model_settings()
                leader_model_config = leader_config.model_config_data if leader_config else None
                leader_fallback_names = (
                    leader_model_config.fallback if leader_model_config else runtime_model_settings.fallback_models
                )
                leader_model_options = AgentModelOptions(
                    temperature=leader_model_config.temperature if leader_model_config else None,
                    top_p=leader_model_config.top_p if leader_model_config else None,
                    max_tokens=leader_model_config.max_tokens if leader_model_config else None,
                    max_react_iters=leader_model_config.max_react_iters if leader_model_config else 40,
                )
                leader_guardrails = _build_runtime_guardrails(leader_config)
                leader_mcp_servers = _build_runtime_mcp_servers(leader_config)
                leader_knowledge_tools = _build_runtime_knowledge_tools(leader_config)
                skill_cache_key = ",".join(sorted(item.id for item in skill_runs))
                fallback_cache_key = ",".join(leader_fallback_names)
                mcp_cache_key = ",".join(
                    f"{server.name}:{','.join(server.enable_tools)}"
                    for server in leader_mcp_servers
                )
                knowledge_cache_key = ",".join(tool.id for tool in leader_knowledge_tools)
                guardrail_cache_key = _guardrail_cache_key(leader_guardrails)
                runtime_agent_id = (
                    f"{leader_agent_id}:{interaction.session_id}:{interaction.model_name}:"
                    f"thinking-{chat_data.deep_thinking}:fallbacks-{fallback_cache_key}:"
                    f"skills-{skill_cache_key}:mcp-{mcp_cache_key}:knowledge-{knowledge_cache_key}:"
                    f"guardrails-{guardrail_cache_key}"
                )
                leader_agent = get_or_create_agent(
                    agent_id=runtime_agent_id,
                    name=leader_agent_name,
                    system_prompt=leader_system_prompt,
                    model_name=interaction.model_name,
                    fallback_names=leader_fallback_names,
                    thinking_enable=chat_data.deep_thinking,
                    permission_mode=_permission_mode_for_guardrails(leader_guardrails),
                    model_options=leader_model_options,
                    guardrails=leader_guardrails,
                    mcp_servers=leader_mcp_servers,
                    knowledge_tools=leader_knowledge_tools,
                )
                leader_activity = _find_team_activity(team_activities, leader_agent_id)
                if leader_activity:
                    _replace_team_activity(
                        team_activities,
                        leader_activity.model_copy(update={
                            "status": "running",
                            "detail": f"Worker 已完成，Leader 正在基于 {len(worker_outputs)} 个 Worker 输出汇总最终回复。",
                            "leader_context_count": len(worker_outputs),
                        }),
                    )
                    yield _sse_event("TEAM_ACTIVITY", {
                        "activities": [item.model_dump(by_alias=True) for item in team_activities],
                    })

                user_msg = UserMsg(
                    name="user",
                    content=_build_user_content(
                        _build_leader_user_message(chat_data.message, worker_outputs),
                        chat_data.images,
                    ),
                )
                assistant_tool_calls: list[ChatToolRun] = []
                async for event in leader_agent.reply_stream(user_msg):
                    event_data = event.model_dump(mode="json")
                    event_type = event_data.pop("type", "UNKNOWN")
                    _attach_provider_cache_usage(event_type, event_data)
                    if event_type == "MODEL_CALL_END":
                        cache_entry = _model_cache_from_event(event_data, interaction.model_name)
                        if cache_entry:
                            assistant_model_caches.append(cache_entry)
                    _record_agent_scope_event(
                        session_id=interaction.session_id,
                        event_type=event_type,
                        event_data=event_data,
                        persist=True,
                        scope="leader",
                        agent_id=leader_agent_id,
                        agent_name=leader_agent_name,
                        team_activity_id=None,
                    )
                    _record_permission_decision(
                        session_id=interaction.session_id,
                        event_type=event_type,
                        event_data=event_data,
                        persist=True,
                        scope="leader",
                        agent_id=leader_agent_id,
                        agent_name=leader_agent_name,
                        team_activity_id=None,
                        guardrails=leader_guardrails,
                    )
                    runtime_event = runtime_event_from_agentscope_sse(
                        event_type,
                        session_id=interaction.session_id,
                        scope="leader",
                        agent_id=leader_agent_id,
                        agent_name=leader_agent_name,
                        event_data=event_data,
                    )
                    if runtime_event:
                        yield runtime_event
                    next_tool_calls = _update_worker_tool_calls(
                        assistant_tool_calls,
                        event_type,
                        event_data,
                    )
                    if next_tool_calls is not assistant_tool_calls:
                        assistant_tool_calls = next_tool_calls
                    _record_tool_execution_artifacts(
                        session_id=interaction.session_id,
                        event_type=event_type,
                        event_data=event_data,
                        persist=True,
                        scope="leader",
                        agent_id=leader_agent_id,
                        agent_name=leader_agent_name,
                        team_activity_id=None,
                        guardrails=leader_guardrails,
                        tool_calls=assistant_tool_calls,
                    )
                    artifact_event = build_tool_artifact_runtime_event(
                        event_type=event_type,
                        event_data=event_data,
                        tool_calls=assistant_tool_calls,
                    )
                    if artifact_event:
                        artifact_type, artifact_payload = artifact_event
                        yield runtime_event_sse(
                            artifact_type,
                            session_id=interaction.session_id,
                            run_id=interaction.run_id,
                            scope="artifact",
                            source="runtime",
                            source_event_type=event_type,
                            agent_id=leader_agent_id,
                            agent_name=leader_agent_name,
                            status=str(artifact_payload.get("status") or "completed"),
                            payload=artifact_payload,
                        )
                    if event_type in HITL_EVENT_TYPES:
                        _mark_persistent_skill_run(
                            leader_agent_id,
                            status="waiting_user",
                            current_step="waiting_hitl_resume",
                        )
                        next_interaction = _create_pending_interaction(
                            interaction_type=event_type,
                            agent=leader_agent,
                            session_id=interaction.session_id,
                            agent_id=leader_agent_id,
                            agent_name=leader_agent_name,
                            model_name=interaction.model_name,
                            reply_id=str(event_data.get("reply_id") or ""),
                            tool_calls=list(event_data.get("tool_calls") or []),
                            message=chat_data.message,
                            assistant_text=assistant_text,
                            assistant_tool_calls=assistant_tool_calls,
                            skill_runs=skill_runs,
                            team_activities=team_activities,
                            chat_data=interaction.chat_data,
                            leader_system_prompt=leader_system_prompt,
                        )
                        yield runtime_event_sse(
                            "AGENT_WAITING_HUMAN",
                            session_id=interaction.session_id,
                            run_id=interaction.run_id,
                            scope="leader",
                            source="runtime",
                            source_event_type=event_type,
                            agent_id=leader_agent_id,
                            agent_name=leader_agent_name,
                            status="waiting",
                            reason="hitl_required",
                            payload={"interactionId": next_interaction.id, "interactionType": event_type},
                        )
                        yield _sse_event("HITL_REQUIRED", _hitl_payload(next_interaction))
                        return
                    if event_type == "TEXT_BLOCK_DELTA":
                        assistant_text += str(event_data.get("delta", ""))
                    elif event_type == "THINKING_BLOCK_DELTA":
                        assistant_thinking += str(event_data.get("delta", ""))
                    if event_type == "REPLY_END" and skill_runs:
                        completed_skill_runs = _set_skill_run_status(skill_runs, "completed")
                        for skill in completed_skill_runs:
                            yield runtime_event_sse(
                                "SKILL_COMPLETED",
                                session_id=interaction.session_id,
                                run_id=interaction.run_id,
                                scope="skill",
                                agent_id=leader_agent_id,
                                agent_name=leader_agent_name,
                                status="completed",
                                payload=skill.model_dump(by_alias=True),
                            )
                        yield _sse_event("SKILL_END", {
                            "skills": [item.model_dump(by_alias=True) for item in completed_skill_runs],
                            "status": "completed",
                        })
                    yield _sse_event(event_type, event_data)

                completed_skill_runs = _set_skill_run_status(skill_runs, "completed")
                _mark_persistent_skill_run(
                    leader_agent_id,
                    status="completed",
                    current_step="completed",
                )
                completed_team_activities = _finalize_team_activities(
                    team_activities,
                    leader_agent_id=leader_agent_id,
                    leader_output=assistant_text,
                )
                _append_assistant_message(
                    session_id=interaction.session_id,
                    agent_id=leader_agent_id,
                    agent_name=leader_agent_name,
                    model_name=interaction.model_name,
                    content=assistant_text,
                    error=assistant_error,
                    thinking=assistant_thinking,
                    skill_calls=completed_skill_runs,
                    tool_calls=assistant_tool_calls,
                    team_activities=completed_team_activities,
                    model_caches=assistant_model_caches,
                )
                _record_run_event(
                    session_id=interaction.session_id,
                    event_type="ASSISTANT_MESSAGE",
                    persist=True,
                    scope="leader",
                    agent_id=leader_agent_id,
                    agent_name=leader_agent_name,
                    status="completed",
                    title="Assistant response",
                    content=_trim_text(assistant_text, 1200),
                    payload={
                        "model": interaction.model_name,
                        "toolCallCount": len(assistant_tool_calls),
                        "skillIds": [item.id for item in completed_skill_runs],
                        "resumedFromWorkerHitl": True,
                    },
                )
                yield _sse_event("TEAM_ACTIVITY", {
                    "activities": [item.model_dump(by_alias=True) for item in completed_team_activities],
                })
                yield runtime_event_sse(
                    "TEAM_COMPLETED",
                    session_id=interaction.session_id,
                    run_id=interaction.run_id,
                    scope="team",
                    agent_id=leader_agent_id,
                    agent_name=leader_agent_name,
                    status="completed",
                    payload={"workerCount": len(worker_outputs)},
                )
                yield runtime_event_sse(
                    "RUN_COMPLETED",
                    session_id=interaction.session_id,
                    run_id=interaction.run_id,
                    scope="run",
                    agent_id=leader_agent_id,
                    agent_name=leader_agent_name,
                    status="completed",
                    payload={"resumedFromWorkerHitl": True},
                )
                return

            assistant_tool_calls = _pending_tool_calls_to_runs(
                interaction.tool_calls,
                interaction.assistant_tool_calls,
            )
            interaction_guardrails = _build_runtime_guardrails(get_registered_agent(interaction.agent_id))
            async for event in agent.reply_stream(resume_event):
                event_data = event.model_dump(mode="json")
                event_type = event_data.pop("type", "UNKNOWN")
                _attach_provider_cache_usage(event_type, event_data)
                if event_type == "MODEL_CALL_END":
                    cache_entry = _model_cache_from_event(event_data, interaction.model_name)
                    if cache_entry:
                        assistant_model_caches.append(cache_entry)
                _record_agent_scope_event(
                    session_id=interaction.session_id,
                    event_type=event_type,
                    event_data=event_data,
                    persist=True,
                    scope=interaction.scope,
                    agent_id=interaction.agent_id,
                    agent_name=interaction.agent_name,
                    team_activity_id=activity.id if activity else None,
                )
                _record_permission_decision(
                    session_id=interaction.session_id,
                    event_type=event_type,
                    event_data=event_data,
                    persist=True,
                    scope=interaction.scope,
                    agent_id=interaction.agent_id,
                    agent_name=interaction.agent_name,
                    team_activity_id=activity.id if activity else None,
                    guardrails=interaction_guardrails,
                )
                runtime_event = runtime_event_from_agentscope_sse(
                    event_type,
                    session_id=interaction.session_id,
                    scope=interaction.scope,
                    agent_id=interaction.agent_id,
                    agent_name=interaction.agent_name,
                    team_activity_id=activity.id if activity else None,
                    event_data=event_data,
                )
                if runtime_event:
                    yield runtime_event
                next_tool_calls = _update_worker_tool_calls(
                    assistant_tool_calls,
                    event_type,
                    event_data,
                )
                if next_tool_calls is not assistant_tool_calls:
                    assistant_tool_calls = next_tool_calls
                _record_tool_execution_artifacts(
                    session_id=interaction.session_id,
                    event_type=event_type,
                    event_data=event_data,
                    persist=True,
                    scope=interaction.scope,
                    agent_id=interaction.agent_id,
                    agent_name=interaction.agent_name,
                    team_activity_id=activity.id if activity else None,
                    guardrails=interaction_guardrails,
                    tool_calls=assistant_tool_calls,
                )
                artifact_event = build_tool_artifact_runtime_event(
                    event_type=event_type,
                    event_data=event_data,
                    tool_calls=assistant_tool_calls,
                )
                if artifact_event:
                    artifact_type, artifact_payload = artifact_event
                    yield runtime_event_sse(
                        artifact_type,
                        session_id=interaction.session_id,
                        run_id=interaction.run_id,
                        scope="artifact",
                        source="runtime",
                        source_event_type=event_type,
                        agent_id=interaction.agent_id,
                        agent_name=interaction.agent_name,
                        team_activity_id=activity.id if activity else None,
                        status=str(artifact_payload.get("status") or "completed"),
                        payload=artifact_payload,
                    )
                if event_type in HITL_EVENT_TYPES:
                    _mark_persistent_skill_run(
                        interaction.agent_id,
                        status="waiting_user",
                        current_step="waiting_hitl_resume",
                    )
                    next_interaction = _create_pending_interaction(
                        interaction_type=event_type,
                        agent=agent,
                        session_id=interaction.session_id,
                        agent_id=interaction.agent_id,
                        agent_name=interaction.agent_name,
                        model_name=interaction.model_name,
                        reply_id=str(event_data.get("reply_id") or ""),
                        tool_calls=list(event_data.get("tool_calls") or []),
                        message=interaction.message,
                        assistant_text=assistant_text,
                        assistant_tool_calls=assistant_tool_calls,
                        skill_runs=skill_runs,
                        team_activities=team_activities,
                    )
                    _clear_pending_interaction(interaction.id)
                    yield runtime_event_sse(
                        "AGENT_WAITING_HUMAN",
                        session_id=interaction.session_id,
                        run_id=interaction.run_id,
                        scope=interaction.scope,
                        source="runtime",
                        source_event_type=event_type,
                        agent_id=interaction.agent_id,
                        agent_name=interaction.agent_name,
                        team_activity_id=activity.id if activity else None,
                        status="waiting",
                        reason="hitl_required",
                        payload={"interactionId": next_interaction.id, "interactionType": event_type},
                    )
                    yield _sse_event("HITL_REQUIRED", _hitl_payload(next_interaction))
                    return

                if event_type == "TEXT_BLOCK_DELTA":
                    assistant_text += str(event_data.get("delta", ""))
                elif event_type == "THINKING_BLOCK_DELTA":
                    assistant_thinking += str(event_data.get("delta", ""))

                if event_type == "REPLY_END" and skill_runs:
                    completed_skill_runs = _set_skill_run_status(skill_runs, "completed")
                    for skill in completed_skill_runs:
                        yield runtime_event_sse(
                            "SKILL_COMPLETED",
                            session_id=interaction.session_id,
                            run_id=interaction.run_id,
                            scope="skill",
                            agent_id=interaction.agent_id,
                            agent_name=interaction.agent_name,
                            status="completed",
                            payload=skill.model_dump(by_alias=True),
                        )
                    yield _sse_event("SKILL_END", {
                        "skills": [item.model_dump(by_alias=True) for item in completed_skill_runs],
                        "status": "completed",
                    })

                yield _sse_event(event_type, event_data)

            completed_skill_runs = _set_skill_run_status(skill_runs, "completed")
            _mark_persistent_skill_run(
                interaction.agent_id,
                status="completed",
                current_step="completed",
            )
            completed_team_activities = _finalize_team_activities(
                team_activities,
                leader_agent_id=interaction.agent_id,
                leader_output=assistant_text,
            )
            _append_assistant_message(
                session_id=interaction.session_id,
                agent_id=interaction.agent_id,
                agent_name=interaction.agent_name,
                model_name=interaction.model_name,
                content=assistant_text,
                error=assistant_error,
                thinking=assistant_thinking,
                skill_calls=completed_skill_runs,
                tool_calls=assistant_tool_calls,
                team_activities=completed_team_activities,
                model_caches=assistant_model_caches,
            )
            _record_run_event(
                session_id=interaction.session_id,
                event_type="ASSISTANT_MESSAGE",
                persist=True,
                scope=interaction.scope,
                agent_id=interaction.agent_id,
                agent_name=interaction.agent_name,
                team_activity_id=activity.id if activity else None,
                status="completed",
                title="Assistant response",
                content=_trim_text(assistant_text, 1200),
                payload={
                    "model": interaction.model_name,
                    "toolCallCount": len(assistant_tool_calls),
                    "skillIds": [item.id for item in completed_skill_runs],
                    "resumedFromHitl": True,
                },
            )
            if team_activities:
                yield _sse_event("TEAM_ACTIVITY", {
                    "activities": [item.model_dump(by_alias=True) for item in completed_team_activities],
                })
            _clear_pending_interaction(interaction.id)
            yield runtime_event_sse(
                "RUN_COMPLETED",
                session_id=interaction.session_id,
                run_id=interaction.run_id,
                scope="run",
                agent_id=interaction.agent_id,
                agent_name=interaction.agent_name,
                status="completed",
                payload={"resumedFromHitl": True},
            )
        except Exception as exc:
            assistant_error = f"Agent 恢复执行异常: {exc}"
            _append_assistant_message(
                session_id=interaction.session_id,
                agent_id=interaction.agent_id,
                agent_name=interaction.agent_name,
                model_name=interaction.model_name,
                content=assistant_text,
                error=assistant_error,
                thinking=assistant_thinking,
                skill_calls=_set_skill_run_status(skill_runs, "failed"),
                team_activities=_set_team_activity_status(team_activities, "failed"),
                model_caches=assistant_model_caches,
            )
            _clear_pending_interaction(interaction.id)
            yield runtime_event_sse(
                "RUN_FAILED",
                session_id=interaction.session_id,
                run_id=interaction.run_id,
                scope="run",
                agent_id=interaction.agent_id,
                agent_name=interaction.agent_name,
                status="failed",
                reason="resume_error",
                payload={"message": assistant_error},
            )
            yield _sse_event("ERROR", {"message": assistant_error})

    pending_interaction = PENDING_INTERACTIONS.get(data.interaction_id)
    stream = (
        relay_chat_background_run_events(pending_interaction.run_id, event_stream())
        if pending_interaction and pending_interaction.run_id
        else event_stream()
    )

    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
