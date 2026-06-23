"""Chat 运行完成/失败后的持久化与收尾。"""

from typing import Any, AsyncGenerator

from src.server.routes.memory import append_agent_memory_event, maybe_summarize_agent_run
from src.server.routes.chat.helpers import _set_skill_run_status, _sse_event
from src.server.routes.chat.models import ChatModelCacheRun, ChatPlanRun, ChatSkillRun, ChatSubagentCall, ChatTeamActivity, ChatToolRun
from src.server.routes.chat.plans import (
    complete_runtime_plan,
    create_plan_approval,
    fail_runtime_plan,
    serialize_plan_for_event,
)
from src.server.routes.chat.skill_runs import update_skill_run_status
from src.server.routes.chat.store import _append_assistant_message
from src.server.routes.chat.team_state import _finalize_team_activities, _set_team_activity_status
from src.server.routes.chat.trace_recorder import _record_run_event, _trim_text


async def handle_plan_approval_if_required(
    *,
    runtime_plan: dict[str, Any] | None,
    agent_config: object | None,
    session_id: str,
    persist: bool,
    agent_id: str,
    agent_name: str,
    model_name: str,
    skill_runs: list[ChatSkillRun],
    team_activities: list[ChatTeamActivity],
    assistant_model_caches: list[ChatModelCacheRun],
) -> AsyncGenerator[str | tuple[str, dict[str, Any] | None], None]:
    """广播 Plan、按配置等待审批；最后 yield 一个内部 result 元组。"""

    if not runtime_plan:
        yield ("continue", runtime_plan)
        return

    yield _sse_event("PLAN_UPDATE", {
        "plan": serialize_plan_for_event(runtime_plan),
    })
    _record_run_event(
        session_id=session_id,
        event_type="PLAN_UPDATE",
        persist=persist,
        agent_id=agent_id,
        agent_name=agent_name,
        status=str(runtime_plan.get("status") or "running"),
        title=str(runtime_plan.get("title") or "Plan Runtime"),
        content=str(runtime_plan.get("markdown") or ""),
        payload={"plan": serialize_plan_for_event(runtime_plan)},
    )

    plan_config = getattr(agent_config, "plan_config", None)
    if not bool(getattr(plan_config, "require_user_approval", False)):
        yield ("continue", runtime_plan)
        return

    approval_id, approval_future = create_plan_approval(runtime_plan)
    approval_payload = {
        "interactionId": approval_id,
        "type": "PLAN_APPROVAL",
        "sessionId": session_id,
        "agentId": agent_id,
        "agentName": agent_name,
        "replyId": "",
        "runId": "",
        "scope": "leader",
        "teamActivityId": None,
        "createdAt": runtime_plan.get("createdAt"),
        "toolCalls": [{
            "id": str(runtime_plan.get("id")),
            "name": "Plan Runtime",
            "input": {
                "title": runtime_plan.get("title"),
                "goal": runtime_plan.get("goal"),
                "steps": [
                    {
                        "title": step.get("title"),
                        "description": step.get("description"),
                        "status": step.get("status"),
                    }
                    for step in runtime_plan.get("steps", [])
                ],
            },
        }],
    }
    _record_run_event(
        session_id=session_id,
        event_type="PLAN_APPROVAL_REQUIRED",
        persist=persist,
        agent_id=agent_id,
        agent_name=agent_name,
        status="waiting",
        title="Plan approval required",
        payload=approval_payload,
    )
    yield _sse_event("PLAN_APPROVAL_REQUIRED", approval_payload)
    approval_action = await approval_future
    _record_run_event(
        session_id=session_id,
        event_type="PLAN_APPROVAL_RESOLVED",
        persist=persist,
        agent_id=agent_id,
        agent_name=agent_name,
        status="completed" if approval_action == "approve" else "rejected",
        title=f"Plan approval {approval_action}",
        payload={"interactionId": approval_id, "action": approval_action},
    )
    yield _sse_event("PLAN_APPROVAL_RESOLVED", {
        "interactionId": approval_id,
        "action": approval_action,
    })
    if approval_action == "approve":
        yield ("continue", runtime_plan)
        return

    assistant_error = "用户拒绝计划，执行已停止。"
    failed_plan = fail_runtime_plan(runtime_plan, error=assistant_error)
    yield _sse_event("PLAN_UPDATE", {
        "plan": serialize_plan_for_event(failed_plan),
    })
    if persist:
        _append_assistant_message(
            session_id=session_id,
            agent_id=agent_id,
            agent_name=agent_name,
            model_name=model_name,
            content="计划未获批准，本轮执行已停止。",
            error=assistant_error,
            skill_calls=_set_skill_run_status(skill_runs, "failed"),
            team_activities=_set_team_activity_status(team_activities, "failed"),
            model_caches=assistant_model_caches,
            plan_runs=[
                ChatPlanRun.model_validate(failed_plan)
            ] if failed_plan else [],
        )
    yield _sse_event("ERROR", {"message": assistant_error})
    yield ("stop", failed_plan)


async def emit_failed_startup_run(
    *,
    session_id: str,
    persist: bool,
    agent_id: str,
    agent_name: str,
    model_name: str,
    assistant_error: str,
    skill_runs: list[ChatSkillRun],
    team_activities: list[ChatTeamActivity],
    assistant_model_caches: list[ChatModelCacheRun] | None = None,
) -> AsyncGenerator[str, None]:
    """启动阶段失败的统一持久化和 UI 事件。"""

    failed_skill_runs = _set_skill_run_status(skill_runs, "failed")
    if persist:
        _append_assistant_message(
            session_id=session_id,
            agent_id=agent_id,
            agent_name=agent_name,
            model_name=model_name,
            content="",
            error=assistant_error,
            skill_calls=failed_skill_runs,
            team_activities=_set_team_activity_status(team_activities, "failed"),
            model_caches=assistant_model_caches or [],
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


def _extract_subagent_calls(tool_calls: list[ChatToolRun]) -> list[ChatSubagentCall]:
    """从工具调用中抽取独立 Subagent 调用记录。"""

    calls: list[ChatSubagentCall] = []
    for tool_call in tool_calls:
        if not tool_call.name.startswith(("CallAgent_", "子 Agent：")):
            continue
        calls.append(ChatSubagentCall(
            id=tool_call.id,
            name=tool_call.name,
            args=tool_call.args,
            result=tool_call.result,
            status=tool_call.status,
        ))
    return calls


async def finalize_successful_leader_run(
    *,
    session_id: str,
    persist: bool,
    agent_id: str,
    agent_name: str,
    model_name: str,
    user_message: str,
    assistant_text: str,
    assistant_thinking: str,
    assistant_tool_calls: list[ChatToolRun],
    assistant_model_caches: list[ChatModelCacheRun],
    skill_runs: list[ChatSkillRun],
    active_skill_run: dict | None,
    team_activities: list[ChatTeamActivity],
    runtime_plan: dict[str, Any] | None,
    team_id: str | None,
    team_name: str | None,
) -> AsyncGenerator[str, None]:
    """完成 Leader run 成功路径的消息、trace、记忆和 UI 事件。"""

    completed_skill_runs = _set_skill_run_status(skill_runs, "completed")
    if active_skill_run:
        update_skill_run_status(
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
    assistant_subagent_calls = _extract_subagent_calls(assistant_tool_calls)
    if runtime_plan:
        runtime_plan = complete_runtime_plan(runtime_plan, result=assistant_text or "任务已完成。")
        yield _sse_event("PLAN_UPDATE", {
            "plan": serialize_plan_for_event(runtime_plan),
        })

    if persist:
        _append_assistant_message(
            session_id=session_id,
            agent_id=agent_id,
            agent_name=agent_name,
            model_name=model_name,
            content=assistant_text,
            error=None,
            thinking=assistant_thinking,
            skill_calls=completed_skill_runs,
            tool_calls=assistant_tool_calls,
            subagent_calls=assistant_subagent_calls,
            team_activities=completed_team_activities,
            model_caches=assistant_model_caches,
            plan_runs=[
                ChatPlanRun.model_validate(runtime_plan)
            ] if runtime_plan else [],
        )
        _record_run_event(
            session_id=session_id,
            event_type="ASSISTANT_MESSAGE",
            persist=persist,
            scope="leader",
            agent_id=agent_id,
            agent_name=agent_name,
            status="completed",
            title="Assistant response",
            content=_trim_text(assistant_text, 1200),
            payload={
                "model": model_name,
                "toolCallCount": len(assistant_tool_calls),
                "subagentCallCount": len(assistant_subagent_calls),
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
    if persist:
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
            user_message=user_message,
            assistant_text=assistant_text,
            metadata={
                "agentName": agent_name,
                "teamId": team_id,
                "teamName": team_name,
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


async def finalize_failed_leader_run(
    *,
    session_id: str,
    persist: bool,
    agent_id: str,
    agent_name: str,
    model_name: str,
    assistant_text: str,
    assistant_error: str,
    assistant_model_caches: list[ChatModelCacheRun],
    skill_runs: list[ChatSkillRun],
    team_activities: list[ChatTeamActivity],
    runtime_plan: dict[str, Any] | None,
) -> AsyncGenerator[str, None]:
    """完成 Leader run 失败路径的持久化与 UI 事件。"""

    if runtime_plan:
        runtime_plan = fail_runtime_plan(runtime_plan, error=assistant_error)
    failed_skill_runs = _set_skill_run_status(skill_runs, "failed")
    _record_run_event(
        session_id=session_id,
        event_type="ERROR",
        persist=persist,
        scope="leader",
        agent_id=agent_id,
        agent_name=agent_name,
        status="failed",
        title="Agent execution failed",
        content=assistant_error,
        payload={"model": model_name},
    )
    if persist:
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
            plan_runs=[
                ChatPlanRun.model_validate(runtime_plan)
            ] if runtime_plan else [],
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
