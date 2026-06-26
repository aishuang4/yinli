"""Chat 运行完成/失败后的持久化与收尾。"""

from typing import AsyncGenerator

from src.server.routes.chat.helpers import _set_skill_run_status, _sse_event
from src.server.routes.chat.models import ChatModelCacheRun, ChatSkillRun, ChatTeamActivity, ChatToolRun
from src.server.routes.chat.skill_runs import update_skill_run_status
from src.server.routes.chat.store import _append_assistant_message
from src.server.routes.chat.team_state import _finalize_team_activities, _set_team_activity_status
from src.server.routes.chat.trace_recorder import _record_run_event, _trim_text


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
    team_id: str | None,
    team_name: str | None,
) -> AsyncGenerator[str, None]:
    """完成 Leader run 成功路径的消息、trace 和 UI 事件。"""

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
            team_activities=completed_team_activities,
            model_caches=assistant_model_caches,
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
) -> AsyncGenerator[str, None]:
    """完成 Leader run 失败路径的持久化与 UI 事件。"""

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
