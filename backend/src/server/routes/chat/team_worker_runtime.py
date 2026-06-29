"""AgentScope-style Team Worker Runtime。

单个 Worker 的职责是：在独立 ReAct 循环中完成 Leader 分派的任务，并把最终
输出作为 TeamSay-style report 回报给 Leader。调度、依赖判断和并发控制不在
本模块处理。
"""

import asyncio
from dataclasses import dataclass

from agentscope.message import UserMsg

from src.agents.factory import AgentModelOptions, get_or_create_agent
from src.config.runtime_settings import get_effective_model_settings
from src.server.routes.agents import get_registered_agent
from src.server.routes.chat.helpers import _sse_event, _trim_text
from src.server.routes.chat.hitl import _create_pending_interaction, _hitl_payload
from src.server.routes.chat.models import (
    HITL_EVENT_TYPES,
    ChatMessage,
    ChatSkillRun,
    ChatTeamActivity,
    ChatTeamMemberPayload,
    ChatToolRun,
)
from src.server.routes.chat.prompts import (
    _build_runtime_context_prompt,
    _build_worker_system_prompt,
    _build_worker_user_message,
)
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
from src.server.routes.chat.team_state import (
    _find_team_activity,
    _replace_team_activity,
    _update_worker_tool_calls,
)
from src.server.routes.chat.trace_recorder import (
    _attach_provider_cache_usage,
    build_tool_artifact_runtime_event,
    _make_fallback_trace_recorder,
    _record_agent_scope_event,
    _record_permission_decision,
    _record_run_event,
    _record_tool_execution_artifacts,
    _record_workspace_event,
)
from src.server.routes.skills import (
    build_skill_system_prompt_from_skills,
    select_relevant_skills,
)


@dataclass
class TeamWorkerRuntimeRequest:
    """运行单个 Team Worker 所需上下文。"""

    data: ChatMessage
    session_id: str
    team_activities: list[ChatTeamActivity]
    model_name: str
    leader_system_prompt: str
    skill_runs: list[ChatSkillRun]
    member: ChatTeamMemberPayload
    dependency_outputs: list[dict[str, str]]
    queue: asyncio.Queue[str | None]
    pause_event: asyncio.Event


async def run_team_worker(request: TeamWorkerRuntimeRequest) -> None:
    """运行一个 Worker，并把 SSE 字符串推入共享队列。"""

    data = request.data
    member = request.member
    team_activities = request.team_activities
    activity = _find_team_activity(team_activities, member.agent_id)
    if activity is None:
        await request.queue.put(None)
        return

    worker_config = get_registered_agent(member.agent_id)
    runtime_model_settings = get_effective_model_settings()
    worker_name = worker_config.name if worker_config else member.name
    worker_prompt = worker_config.system_prompt if worker_config else member.description
    worker_skill_ids = worker_config.skills if worker_config else []
    worker_model_config = worker_config.model_config_data if worker_config else None
    worker_model_options = AgentModelOptions(
        temperature=worker_model_config.temperature if worker_model_config else None,
        top_p=worker_model_config.top_p if worker_model_config else None,
        max_tokens=worker_model_config.max_tokens if worker_model_config else None,
        max_react_iters=worker_model_config.max_react_iters if worker_model_config else 40,
    )
    worker_guardrails = _build_runtime_guardrails(worker_config)
    worker_mcp_servers = _build_runtime_mcp_servers(worker_config)
    worker_knowledge_tools = _build_runtime_knowledge_tools(worker_config)
    worker_skills = select_relevant_skills(worker_skill_ids, data.message)
    worker_skill_prompt = build_skill_system_prompt_from_skills(worker_skills)
    worker_system_prompt = "\n\n".join(
        part
        for part in [
            worker_prompt or "You are a helpful worker agent.",
            _build_runtime_context_prompt(),
            _build_worker_system_prompt(data.team_name, data.agent_name, member),
            worker_skill_prompt,
        ]
        if part
    )
    worker_cache_key = ",".join(sorted(skill.id for skill in worker_skills))
    worker_fallback_names = worker_model_config.fallback if worker_model_config else runtime_model_settings.fallback_models
    worker_fallback_cache_key = ",".join(worker_fallback_names)
    worker_mcp_cache_key = ",".join(
        f"{server.name}:{','.join(server.enable_tools)}"
        for server in worker_mcp_servers
    )
    worker_knowledge_cache_key = ",".join(tool.id for tool in worker_knowledge_tools)
    runtime_worker_id = (
        f"{member.agent_id}:{request.session_id}:{request.model_name}:team-worker:"
        f"thinking-{data.deep_thinking}:fallbacks-{worker_fallback_cache_key}:"
        f"skills-{worker_cache_key}:mcp-{worker_mcp_cache_key}:"
        f"knowledge-{worker_knowledge_cache_key}:"
        f"guardrails-{_guardrail_cache_key(worker_guardrails)}"
    )
    worker_agent = get_or_create_agent(
        agent_id=runtime_worker_id,
        name=worker_name,
        system_prompt=worker_system_prompt,
        model_name=request.model_name,
        fallback_names=worker_fallback_names,
        thinking_enable=data.deep_thinking,
        permission_mode=_permission_mode_for_guardrails(worker_guardrails),
        model_options=worker_model_options,
        guardrails=worker_guardrails,
        mcp_servers=worker_mcp_servers,
        knowledge_tools=worker_knowledge_tools,
        on_fallback_event=_make_fallback_trace_recorder(
            session_id=request.session_id,
            persist=not data.sandbox,
            scope="worker",
            agent_id=member.agent_id,
            agent_name=worker_name,
            team_activity_id=activity.id,
        ),
    )
    _record_workspace_event(
        session_id=request.session_id,
        persist=not data.sandbox,
        scope="worker",
        agent_id=member.agent_id,
        agent_name=worker_name,
        team_activity_id=activity.id,
        guardrails=worker_guardrails,
    )

    worker_text = ""
    worker_thinking = ""
    worker_tool_calls: list[ChatToolRun] = []
    event_type = "UNKNOWN"
    event_data: dict = {}

    try:
        worker_msg = UserMsg(
            name="team_leader",
            content=_build_user_content(
                _build_worker_user_message(
                    data.message,
                    member,
                    request.dependency_outputs,
                    assignment=activity.assignment if activity else "",
                    expected_output=activity.expected_output if activity else "",
                    constraints=activity.constraints if activity else "",
                ),
                data.images,
            ),
        )
        async for event in worker_agent.reply_stream(worker_msg):
            event_data = event.model_dump(mode="json")
            event_type = event_data.pop("type", "UNKNOWN")
            _attach_provider_cache_usage(event_type, event_data)
            activity = _find_team_activity(team_activities, member.agent_id)
            _record_agent_scope_event(
                session_id=request.session_id,
                event_type=event_type,
                event_data=event_data,
                persist=not data.sandbox,
                scope="worker",
                agent_id=member.agent_id,
                agent_name=worker_name,
                team_activity_id=activity.id if activity else None,
            )
            _record_permission_decision(
                session_id=request.session_id,
                event_type=event_type,
                event_data=event_data,
                persist=not data.sandbox,
                scope="worker",
                agent_id=member.agent_id,
                agent_name=worker_name,
                team_activity_id=activity.id if activity else None,
                guardrails=worker_guardrails,
            )
            if event_type in HITL_EVENT_TYPES:
                await _pause_worker_for_hitl(
                    request=request,
                    worker_agent=worker_agent,
                    worker_name=worker_name,
                    worker_text=worker_text,
                    worker_thinking=worker_thinking,
                    worker_tool_calls=worker_tool_calls,
                    event_type=event_type,
                    event_data=event_data,
                )
                return

            if event_type == "TEXT_BLOCK_DELTA":
                worker_text += str(event_data.get("delta", ""))
                activity = _find_team_activity(team_activities, member.agent_id)
                if activity:
                    _replace_team_activity(
                        team_activities,
                        activity.model_copy(update={
                            "output": worker_text,
                            "detail": _trim_text(worker_text, 120) or activity.detail,
                        }),
                    )
            elif event_type == "THINKING_BLOCK_DELTA":
                worker_thinking += str(event_data.get("delta", ""))
                activity = _find_team_activity(team_activities, member.agent_id)
                if activity:
                    _replace_team_activity(
                        team_activities,
                        activity.model_copy(update={"thinking": worker_thinking}),
                    )

            next_tool_calls = _update_worker_tool_calls(worker_tool_calls, event_type, event_data)
            if next_tool_calls is not worker_tool_calls:
                worker_tool_calls = next_tool_calls
                activity = _find_team_activity(team_activities, member.agent_id)
                if activity:
                    _replace_team_activity(
                        team_activities,
                        activity.model_copy(update={"tool_calls": worker_tool_calls}),
                    )

            await _emit_worker_runtime_event(
                request=request,
                worker_name=worker_name,
                worker_guardrails=worker_guardrails,
                worker_tool_calls=worker_tool_calls,
                event_type=event_type,
                event_data=event_data,
            )

        await _complete_worker_report(
            request=request,
            worker_name=worker_name,
            worker_text=worker_text,
            worker_thinking=worker_thinking,
            worker_tool_calls=worker_tool_calls,
        )
    except Exception as exc:
        await _fail_worker(
            request=request,
            worker_name=worker_name,
            worker_text=worker_text,
            worker_thinking=worker_thinking,
            worker_tool_calls=worker_tool_calls,
            event_type=event_type,
            event_data=event_data,
            worker_guardrails=worker_guardrails,
            error=exc,
        )
    finally:
        await request.queue.put(None)


async def _pause_worker_for_hitl(
    *,
    request: TeamWorkerRuntimeRequest,
    worker_agent: object,
    worker_name: str,
    worker_text: str,
    worker_thinking: str,
    worker_tool_calls: list[ChatToolRun],
    event_type: str,
    event_data: dict,
) -> None:
    """Worker 需要人工介入时暂停整个 Team Runtime。"""

    data = request.data
    member = request.member
    activity = _find_team_activity(request.team_activities, member.agent_id)
    if activity:
        _replace_team_activity(
            request.team_activities,
            activity.model_copy(update={
                "status": "waiting",
                "detail": "等待人工确认后继续 Worker ReAct。",
                "output": worker_text,
                "thinking": worker_thinking,
                "tool_calls": worker_tool_calls,
            }),
        )
    interaction = _create_pending_interaction(
        interaction_type=event_type,
        agent=worker_agent,
        session_id=request.session_id,
        agent_id=member.agent_id,
        agent_name=worker_name,
        model_name=request.model_name,
        reply_id=str(event_data.get("reply_id") or ""),
        tool_calls=list(event_data.get("tool_calls") or []),
        message=_build_worker_user_message(
            data.message,
            member,
            request.dependency_outputs,
            assignment=activity.assignment if activity else "",
            expected_output=activity.expected_output if activity else "",
            constraints=activity.constraints if activity else "",
        ),
        assistant_text="",
        skill_runs=request.skill_runs,
        team_activities=request.team_activities,
        scope="worker",
        chat_data=data.model_dump(mode="json"),
        leader_system_prompt=request.leader_system_prompt,
        worker_member=member.model_dump(by_alias=True),
        worker_text=worker_text,
        worker_thinking=worker_thinking,
        worker_tool_calls=worker_tool_calls,
        persist=not data.sandbox,
    )
    await request.queue.put(runtime_event_sse(
        "TEAM_WORKER_WAITING_HUMAN",
        session_id=request.session_id,
        scope="worker",
        source="runtime",
        source_event_type=event_type,
        agent_id=member.agent_id,
        agent_name=worker_name,
        team_activity_id=activity.id if activity else None,
        status="waiting",
        reason="hitl_required",
        payload={"interactionId": interaction.id, "interactionType": event_type},
    ))
    await request.queue.put(runtime_event_sse(
        "AGENT_WAITING_HUMAN",
        session_id=request.session_id,
        scope="worker",
        source="runtime",
        source_event_type=event_type,
        agent_id=member.agent_id,
        agent_name=worker_name,
        team_activity_id=activity.id if activity else None,
        status="waiting",
        reason="hitl_required",
        payload={"interactionId": interaction.id, "interactionType": event_type},
    ))
    await request.queue.put(_sse_event("TEAM_ACTIVITY", {
        "activities": [item.model_dump(by_alias=True) for item in request.team_activities],
    }))
    await request.queue.put(_sse_event("HITL_REQUIRED", _hitl_payload(interaction)))
    request.pause_event.set()


async def _emit_worker_runtime_event(
    *,
    request: TeamWorkerRuntimeRequest,
    worker_name: str,
    worker_guardrails: object,
    worker_tool_calls: list[ChatToolRun],
    event_type: str,
    event_data: dict,
) -> None:
    """记录并转发 Worker 的工具、产物和原始 AgentScope 事件。"""

    member = request.member
    activity = _find_team_activity(request.team_activities, member.agent_id)
    if not activity:
        return
    _record_tool_execution_artifacts(
        session_id=request.session_id,
        event_type=event_type,
        event_data=event_data,
        persist=not request.data.sandbox,
        scope="worker",
        agent_id=member.agent_id,
        agent_name=worker_name,
        team_activity_id=activity.id,
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
        await request.queue.put(runtime_event_sse(
            artifact_type,
            session_id=request.session_id,
            scope="artifact",
            source="runtime",
            source_event_type=event_type,
            agent_id=member.agent_id,
            agent_name=worker_name,
            team_activity_id=activity.id,
            status=str(artifact_payload.get("status") or "completed"),
            payload=artifact_payload,
        ))
    runtime_event = runtime_event_from_agentscope_sse(
        event_type,
        session_id=request.session_id,
        scope="worker",
        agent_id=member.agent_id,
        agent_name=worker_name,
        team_activity_id=activity.id,
        event_data=event_data,
    )
    if runtime_event:
        await request.queue.put(runtime_event)
    await request.queue.put(_sse_event("TEAM_MEMBER_EVENT", {
        "activityId": activity.id,
        "agentId": member.agent_id,
        "agentName": worker_name,
        "role": "worker",
        "eventType": event_type,
        "event": event_data,
    }))


async def _complete_worker_report(
    *,
    request: TeamWorkerRuntimeRequest,
    worker_name: str,
    worker_text: str,
    worker_thinking: str,
    worker_tool_calls: list[ChatToolRun],
) -> None:
    """完成 Worker，并发送 TeamSay-style report 事件。"""

    member = request.member
    activity = _find_team_activity(request.team_activities, member.agent_id)
    if not activity:
        return
    completed = activity.model_copy(update={
        "status": "completed",
        "output": worker_text,
        "report": worker_text,
        "thinking": worker_thinking,
        "tool_calls": worker_tool_calls,
        "detail": _trim_text(worker_text, 160) or "Worker 已完成本轮 ReAct 循环。",
    })
    _replace_team_activity(request.team_activities, completed)
    _record_run_event(
        session_id=request.session_id,
        event_type="TEAM_WORKER_REPORTED",
        persist=not request.data.sandbox,
        scope="worker",
        agent_id=member.agent_id,
        agent_name=worker_name,
        team_activity_id=activity.id,
        status="completed",
        title=f"Worker reported: {worker_name}",
        content=_trim_text(worker_text, 1200),
        payload={
            "reportTo": request.data.agent_name or "Leader",
            "toolCallCount": len(worker_tool_calls),
            "dependencyCount": len(request.dependency_outputs),
        },
    )
    await request.queue.put(runtime_event_sse(
        "TEAM_WORKER_REPORTED",
        session_id=request.session_id,
        scope="worker",
        agent_id=member.agent_id,
        agent_name=worker_name,
        team_activity_id=activity.id,
        status="completed",
        payload={
            "to": request.data.agent_name or "Leader",
            "reportPreview": _trim_text(worker_text, 240),
        },
    ))
    await request.queue.put(runtime_event_sse(
        "TEAM_WORKER_COMPLETED",
        session_id=request.session_id,
        scope="worker",
        agent_id=member.agent_id,
        agent_name=worker_name,
        team_activity_id=activity.id,
        status="completed",
        payload={
            "toolCallCount": len(worker_tool_calls),
            "outputPreview": _trim_text(worker_text, 240),
        },
    ))
    await request.queue.put(_sse_event("TEAM_ACTIVITY", {
        "activities": [item.model_dump(by_alias=True) for item in request.team_activities],
    }))


async def _fail_worker(
    *,
    request: TeamWorkerRuntimeRequest,
    worker_name: str,
    worker_text: str,
    worker_thinking: str,
    worker_tool_calls: list[ChatToolRun],
    event_type: str,
    event_data: dict,
    worker_guardrails: object,
    error: Exception,
) -> None:
    """Worker 异常失败时更新活动、trace 和 Runtime Event。"""

    member = request.member
    activity = _find_team_activity(request.team_activities, member.agent_id)
    if activity:
        _replace_team_activity(
            request.team_activities,
            activity.model_copy(update={
                "status": "failed",
                "detail": f"Worker 执行失败: {error}",
                "output": worker_text,
                "report": worker_text,
                "thinking": worker_thinking,
                "tool_calls": worker_tool_calls,
            }),
        )
        activity = _find_team_activity(request.team_activities, member.agent_id)
        _record_tool_execution_artifacts(
            session_id=request.session_id,
            event_type=event_type,
            event_data=event_data,
            persist=not request.data.sandbox,
            scope="worker",
            agent_id=member.agent_id,
            agent_name=worker_name,
            team_activity_id=activity.id if activity else None,
            guardrails=worker_guardrails,
            tool_calls=worker_tool_calls,
        )
    _record_run_event(
        session_id=request.session_id,
        event_type="WORKER_FAILED",
        persist=not request.data.sandbox,
        scope="worker",
        agent_id=member.agent_id,
        agent_name=worker_name,
        team_activity_id=activity.id if activity else None,
        status="failed",
        title=f"Worker failed: {worker_name}",
        content=str(error),
        payload={"toolCallCount": len(worker_tool_calls)},
    )
    await request.queue.put(runtime_event_sse(
        "TEAM_WORKER_FAILED",
        session_id=request.session_id,
        scope="worker",
        agent_id=member.agent_id,
        agent_name=worker_name,
        team_activity_id=activity.id if activity else None,
        status="failed",
        reason="worker_error",
        payload={"message": str(error), "toolCallCount": len(worker_tool_calls)},
    ))
    await request.queue.put(_sse_event("TEAM_ACTIVITY", {
        "activities": [item.model_dump(by_alias=True) for item in request.team_activities],
    }))
