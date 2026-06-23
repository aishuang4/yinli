"""Team worker 并发编排 — 依赖调度、ReAct 执行、HITL 暂停与 SSE 事件合流。"""

import asyncio
from typing import AsyncGenerator

from agentscope.message import UserMsg

from src.agents.factory import AgentModelOptions, get_or_create_agent
from src.config.runtime_settings import get_effective_model_settings
from src.server.routes.agents import get_registered_agent
from src.server.routes.memory import (
    append_agent_memory_event,
    maybe_summarize_agent_run,
)
from src.server.routes.skills import (
    build_skill_system_prompt_from_skills,
    select_relevant_skills,
)

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
from src.server.routes.chat.plans import mark_plan_step_for_agent, serialize_plan_for_event
from src.server.routes.chat.prompts import (
    _build_agent_memory_prompt,
    _build_agent_plan_prompt,
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
from src.server.routes.chat.team_state import (
    _find_team_activity,
    _member_dependency_ids,
    _member_names,
    _replace_team_activity,
    _update_worker_tool_calls,
)
from src.server.routes.chat.trace_recorder import (
    _attach_provider_cache_usage,
    _make_fallback_trace_recorder,
    _record_agent_scope_event,
    _record_permission_decision,
    _record_run_event,
    _record_tool_execution_artifacts,
    _record_workspace_event,
)


async def _run_team_workers(
    *,
    data: ChatMessage,
    session_id: str,
    team_activities: list[ChatTeamActivity],
    model_name: str,
    leader_system_prompt: str,
    skill_runs: list[ChatSkillRun],
    runtime_plan: dict | None = None,
) -> AsyncGenerator[str, None]:
    """Run worker Agents concurrently and merge their SSE events."""
    workers = [
        member
        for member in data.team_members
        if member.role != "leader"
        and member.agent_id != data.agent_id
        and (
            not (activity := _find_team_activity(team_activities, member.agent_id))
            or activity.status not in {"completed", "waiting"}
        )
    ]
    if not workers:
        return

    queue: asyncio.Queue[str | None] = asyncio.Queue()
    pause_event = asyncio.Event()

    for member in workers:
        activity = _find_team_activity(team_activities, member.agent_id)
        if activity:
            waiting_for = [
                dep
                for dep in _member_dependency_ids(member, workers)
                if dep in {worker.agent_id for worker in workers}
            ]
            _replace_team_activity(
                team_activities,
                        activity.model_copy(update={
                            "status": "pending" if waiting_for else "running",
                            "execution_mode": member.execution_mode,
                            "wait_reason": (
                                f"等待依赖 Worker 完成：{', '.join(_member_names(waiting_for, workers))}"
                                if waiting_for
                                else ""
                            ),
                            "detail": (
                                f"等待依赖 Worker 完成：{', '.join(_member_names(waiting_for, workers))}"
                                if waiting_for
                        else activity.detail
                    ),
                }),
            )

    yield _sse_event("TEAM_ACTIVITY", {
        "activities": [item.model_dump(by_alias=True) for item in team_activities],
    })

    async def run_worker(
        member: ChatTeamMemberPayload,
        dependency_outputs: list[dict[str, str]],
    ) -> None:
        activity = _find_team_activity(team_activities, member.agent_id)
        if activity is None:
            return
        worker_config = get_registered_agent(member.agent_id)
        runtime_model_settings = get_effective_model_settings()
        worker_name = worker_config.name if worker_config else member.name
        worker_prompt = worker_config.system_prompt if worker_config else member.description
        worker_skill_ids = worker_config.skills if worker_config else []
        worker_plan_prompt = _build_agent_plan_prompt(worker_config)
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
        worker_memory_prompt = _build_agent_memory_prompt(member.agent_id)
        worker_runtime_context_prompt = _build_runtime_context_prompt()
        worker_system_prompt = "\n\n".join(
            part
            for part in [
                worker_prompt or "You are a helpful worker agent.",
                worker_runtime_context_prompt,
                worker_plan_prompt,
                worker_memory_prompt,
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
            f"{member.agent_id}:{session_id}:{model_name}:team-worker:"
            f"thinking-{data.deep_thinking}:fallbacks-{worker_fallback_cache_key}:"
            f"skills-{worker_cache_key}:mcp-{worker_mcp_cache_key}:"
            f"knowledge-{worker_knowledge_cache_key}:"
            f"guardrails-{_guardrail_cache_key(worker_guardrails)}"
        )
        worker_agent = get_or_create_agent(
            agent_id=runtime_worker_id,
            name=worker_name,
            system_prompt=worker_system_prompt,
            model_name=model_name,
            fallback_names=worker_fallback_names,
            thinking_enable=data.deep_thinking,
            permission_mode=_permission_mode_for_guardrails(worker_guardrails),
            model_options=worker_model_options,
            guardrails=worker_guardrails,
            mcp_servers=worker_mcp_servers,
            knowledge_tools=worker_knowledge_tools,
            on_fallback_event=_make_fallback_trace_recorder(
                session_id=session_id,
                persist=not data.sandbox,
                scope="worker",
                agent_id=member.agent_id,
                agent_name=worker_name,
                team_activity_id=activity.id if activity else None,
            ),
        )
        _record_workspace_event(
            session_id=session_id,
            persist=not data.sandbox,
            scope="worker",
            agent_id=member.agent_id,
            agent_name=worker_name,
            team_activity_id=activity.id if activity else None,
            guardrails=worker_guardrails,
        )
        worker_text = ""
        worker_thinking = ""
        worker_tool_calls: list[ChatToolRun] = []

        try:
            worker_msg = UserMsg(
                name="team_leader",
                content=_build_user_content(
                    _build_worker_user_message(data.message, member, dependency_outputs),
                    data.images,
                ),
            )
            async for event in worker_agent.reply_stream(worker_msg):
                event_data = event.model_dump(mode="json")
                event_type = event_data.pop("type", "UNKNOWN")
                _attach_provider_cache_usage(event_type, event_data)
                activity = _find_team_activity(team_activities, member.agent_id)
                _record_agent_scope_event(
                    session_id=session_id,
                    event_type=event_type,
                    event_data=event_data,
                    persist=not data.sandbox,
                    scope="worker",
                    agent_id=member.agent_id,
                    agent_name=worker_name,
                    team_activity_id=activity.id if activity else None,
                )
                _record_permission_decision(
                    session_id=session_id,
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
                    interaction = _create_pending_interaction(
                        interaction_type=event_type,
                        agent=worker_agent,
                        session_id=session_id,
                        agent_id=member.agent_id,
                        agent_name=worker_name,
                        model_name=model_name,
                        reply_id=str(event_data.get("reply_id") or ""),
                        tool_calls=list(event_data.get("tool_calls") or []),
                        message=_build_worker_user_message(data.message, member, dependency_outputs),
                        assistant_text="",
                        skill_runs=skill_runs,
                        team_activities=team_activities,
                        scope="worker",
                        chat_data=data.model_dump(mode="json"),
                        leader_system_prompt=leader_system_prompt,
                        worker_member=member.model_dump(by_alias=True),
                        worker_text=worker_text,
                        worker_thinking=worker_thinking,
                        worker_tool_calls=worker_tool_calls,
                        persist=not data.sandbox,
                    )
                    await queue.put(_sse_event("TEAM_ACTIVITY", {
                        "activities": [item.model_dump(by_alias=True) for item in team_activities],
                    }))
                    await queue.put(_sse_event("HITL_REQUIRED", _hitl_payload(interaction)))
                    pause_event.set()
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

                next_tool_calls = _update_worker_tool_calls(
                    worker_tool_calls,
                    event_type,
                    event_data,
                )
                if next_tool_calls is not worker_tool_calls:
                    worker_tool_calls = next_tool_calls
                    activity = _find_team_activity(team_activities, member.agent_id)
                    if activity:
                        _replace_team_activity(
                            team_activities,
                            activity.model_copy(update={"tool_calls": worker_tool_calls}),
                        )

                activity = _find_team_activity(team_activities, member.agent_id)
                if activity:
                    await queue.put(_sse_event("TEAM_MEMBER_EVENT", {
                        "activityId": activity.id,
                        "agentId": member.agent_id,
                        "agentName": worker_name,
                        "role": "worker",
                        "eventType": event_type,
                        "event": event_data,
                    }))

            activity = _find_team_activity(team_activities, member.agent_id)
            if activity:
                completed = activity.model_copy(update={
                    "status": "completed",
                    "output": worker_text,
                    "thinking": worker_thinking,
                    "tool_calls": worker_tool_calls,
                    "detail": _trim_text(worker_text, 160) or "Worker 已完成本轮 ReAct 循环。",
                })
                _replace_team_activity(team_activities, completed)
                updated_plan = mark_plan_step_for_agent(
                    runtime_plan,
                    assigned_agent_id=member.agent_id,
                    status="completed",
                    result=_trim_text(worker_text, 2000) or f"{worker_name} 已完成本轮任务。",
                )
                if updated_plan and updated_plan != runtime_plan:
                    await queue.put(_sse_event("PLAN_UPDATE", {
                        "plan": serialize_plan_for_event(updated_plan),
                    }))
                _record_run_event(
                    session_id=session_id,
                    event_type="WORKER_COMPLETED",
                    persist=not data.sandbox,
                    scope="worker",
                    agent_id=member.agent_id,
                    agent_name=worker_name,
                    team_activity_id=activity.id,
                    status="completed",
                    title=f"Worker completed: {worker_name}",
                    content=_trim_text(worker_text, 1200),
                    payload={
                        "toolCallCount": len(worker_tool_calls),
                        "dependencyCount": len(dependency_outputs),
                    },
                )
            if not data.sandbox:
                append_agent_memory_event(
                    agent_id=member.agent_id,
                    event_type="team_message",
                    title=f"Team Worker 输出: {worker_name}",
                    content=worker_text,
                    session_id=session_id,
                    metadata={
                        "teamId": data.team_id,
                        "teamName": data.team_name,
                        "leaderAgentId": data.agent_id,
                        "model": model_name,
                    },
                )
                worker_memory_facts = await maybe_summarize_agent_run(
                    agent_id=member.agent_id,
                    agent_name=worker_name,
                    session_id=session_id,
                    user_message=_build_worker_user_message(data.message, member, dependency_outputs),
                    assistant_text=worker_text,
                    metadata={
                        "teamId": data.team_id,
                        "teamName": data.team_name,
                        "leaderAgentId": data.agent_id,
                        "model": model_name,
                        "skillIds": [skill.id for skill in worker_skills],
                    },
                )
                if worker_memory_facts:
                    await queue.put(_sse_event("MEMORY_SUMMARY", {
                        "agentId": member.agent_id,
                        "agentName": worker_name,
                        "facts": [fact.model_dump(mode="json") for fact in worker_memory_facts],
                    }))
            await queue.put(_sse_event("TEAM_ACTIVITY", {
                "activities": [item.model_dump(by_alias=True) for item in team_activities],
            }))
        except Exception as exc:
            activity = _find_team_activity(team_activities, member.agent_id)
            if activity:
                _replace_team_activity(
                    team_activities,
                    activity.model_copy(update={
                        "status": "failed",
                        "detail": f"Worker 执行失败: {exc}",
                        "output": worker_text,
                        "thinking": worker_thinking,
                                "tool_calls": worker_tool_calls,
                            }),
                        )
                activity = _find_team_activity(team_activities, member.agent_id)
                updated_plan = mark_plan_step_for_agent(
                    runtime_plan,
                    assigned_agent_id=member.agent_id,
                    status="blocked",
                    result=f"Worker 执行失败: {exc}",
                )
                if updated_plan and updated_plan != runtime_plan:
                    await queue.put(_sse_event("PLAN_UPDATE", {
                        "plan": serialize_plan_for_event(updated_plan),
                    }))
                _record_tool_execution_artifacts(
                    session_id=session_id,
                    event_type=event_type,
                    event_data=event_data,
                    persist=not data.sandbox,
                    scope="worker",
                    agent_id=member.agent_id,
                    agent_name=worker_name,
                    team_activity_id=activity.id if activity else None,
                    guardrails=worker_guardrails,
                    tool_calls=worker_tool_calls,
                )
            _record_run_event(
                session_id=session_id,
                event_type="WORKER_FAILED",
                persist=not data.sandbox,
                scope="worker",
                agent_id=member.agent_id,
                agent_name=worker_name,
                team_activity_id=activity.id if activity else None,
                status="failed",
                title=f"Worker failed: {worker_name}",
                content=str(exc),
                payload={"toolCallCount": len(worker_tool_calls)},
            )
            await queue.put(_sse_event("TEAM_ACTIVITY", {
                "activities": [item.model_dump(by_alias=True) for item in team_activities],
            }))
        finally:
            await queue.put(None)

    pending: dict[str, ChatTeamMemberPayload] = {member.agent_id: member for member in workers}
    running: dict[asyncio.Task[None], str] = {}
    completed_outputs: dict[str, dict[str, str]] = {}
    failed_workers: set[str] = set()
    all_worker_ids = {member.agent_id for member in workers}

    def start_ready_workers() -> None:
        ready: list[ChatTeamMemberPayload] = []
        for member in list(pending.values()):
            dependencies = _member_dependency_ids(member, workers)
            invalid_dependencies = [dep for dep in dependencies if dep not in all_worker_ids]
            if invalid_dependencies:
                pending.pop(member.agent_id, None)
                activity = _find_team_activity(team_activities, member.agent_id)
                if activity:
                    _replace_team_activity(
                        team_activities,
                        activity.model_copy(update={
                            "status": "failed",
                            "detail": "依赖配置无效：找不到指定的上游 Worker。",
                            "wait_reason": "依赖配置无效：找不到指定的上游 Worker。",
                        }),
                    )
                updated_plan = mark_plan_step_for_agent(
                    runtime_plan,
                    assigned_agent_id=member.agent_id,
                    status="blocked",
                    result="依赖配置无效：找不到指定的上游 Worker。",
                )
                if updated_plan and updated_plan != runtime_plan:
                    queue.put_nowait(_sse_event("PLAN_UPDATE", {
                        "plan": serialize_plan_for_event(updated_plan),
                    }))
                failed_workers.add(member.agent_id)
                continue
            failed_dependencies = [dep for dep in dependencies if dep in failed_workers]
            if failed_dependencies:
                pending.pop(member.agent_id, None)
                activity = _find_team_activity(team_activities, member.agent_id)
                if activity:
                    _replace_team_activity(
                        team_activities,
                        activity.model_copy(update={
                            "status": "failed",
                            "detail": f"上游 Worker 失败，跳过执行：{', '.join(_member_names(failed_dependencies, workers))}",
                            "wait_reason": f"上游 Worker 失败：{', '.join(_member_names(failed_dependencies, workers))}",
                        }),
                    )
                updated_plan = mark_plan_step_for_agent(
                    runtime_plan,
                    assigned_agent_id=member.agent_id,
                    status="blocked",
                    result=f"上游 Worker 失败，跳过执行：{', '.join(_member_names(failed_dependencies, workers))}",
                )
                if updated_plan and updated_plan != runtime_plan:
                    queue.put_nowait(_sse_event("PLAN_UPDATE", {
                        "plan": serialize_plan_for_event(updated_plan),
                    }))
                failed_workers.add(member.agent_id)
                continue
            blocking = [dep for dep in dependencies if dep not in completed_outputs and dep not in failed_workers]
            if blocking:
                activity = _find_team_activity(team_activities, member.agent_id)
                if activity:
                    _replace_team_activity(
                        team_activities,
                        activity.model_copy(update={
                            "status": "pending",
                            "detail": f"等待依赖 Worker 完成：{', '.join(_member_names(blocking, workers))}",
                            "wait_reason": f"等待依赖 Worker 完成：{', '.join(_member_names(blocking, workers))}",
                        }),
                    )
                continue
            ready.append(member)

        for member in ready:
            pending.pop(member.agent_id, None)
            dependencies = _member_dependency_ids(member, workers)
            dependency_outputs = [
                completed_outputs[dep]
                for dep in dependencies
                if dep in completed_outputs
            ]
            activity = _find_team_activity(team_activities, member.agent_id)
            if activity:
                _replace_team_activity(
                    team_activities,
                    activity.model_copy(update={
                        "status": "running",
                        "wait_reason": "",
                        "dependency_output_count": len(dependency_outputs),
                        "detail": (
                            f"依赖已满足，开始执行。已接收 {len(dependency_outputs)} 个上游输出。"
                            if dependency_outputs
                            else activity.detail
                        ),
                    }),
                )
            task = asyncio.create_task(run_worker(member, dependency_outputs))
            running[task] = member.agent_id

    start_ready_workers()
    await queue.put(_sse_event("TEAM_ACTIVITY", {
        "activities": [item.model_dump(by_alias=True) for item in team_activities],
    }))

    while running or pending:
        if not running and pending:
            for member in list(pending.values()):
                activity = _find_team_activity(team_activities, member.agent_id)
                if activity:
                    _replace_team_activity(
                        team_activities,
                        activity.model_copy(update={
                            "status": "failed",
                            "detail": "依赖配置形成循环或上游 Worker 失败，无法继续调度。",
                            "wait_reason": "依赖配置形成循环或上游 Worker 失败。",
                        }),
                    )
                updated_plan = mark_plan_step_for_agent(
                    runtime_plan,
                    assigned_agent_id=member.agent_id,
                    status="blocked",
                    result="依赖配置形成循环或上游 Worker 失败，无法继续调度。",
                )
                if updated_plan and updated_plan != runtime_plan:
                    yield _sse_event("PLAN_UPDATE", {
                        "plan": serialize_plan_for_event(updated_plan),
                    })
                failed_workers.add(member.agent_id)
            pending.clear()
            yield _sse_event("TEAM_ACTIVITY", {
                "activities": [item.model_dump(by_alias=True) for item in team_activities],
            })
            break

        item = await queue.get()
        if item is None:
            done_tasks = [task for task in running if task.done()]
            for task in done_tasks:
                worker_id = running.pop(task)
                activity = _find_team_activity(team_activities, worker_id)
                if activity and activity.status == "completed":
                    completed_outputs[worker_id] = {
                        "agentId": activity.agent_id,
                        "name": activity.agent_name,
                        "output": activity.output,
                    }
                elif activity and activity.status in {"failed", "waiting"}:
                    failed_workers.add(worker_id)
            if pause_event.is_set():
                for task in running:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*running.keys(), return_exceptions=True)
                while not queue.empty():
                    queued = queue.get_nowait()
                    if queued is not None:
                        yield queued
                return
            start_ready_workers()
            yield _sse_event("TEAM_ACTIVITY", {
                "activities": [item.model_dump(by_alias=True) for item in team_activities],
            })
            continue
        yield item

    await asyncio.gather(*running.keys(), return_exceptions=True)
