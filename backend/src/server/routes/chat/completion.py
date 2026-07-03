"""Chat 运行完成/失败后的持久化与收尾。"""

import asyncio
from typing import AsyncGenerator

from src.memory.node_persist import MemoryNodePersistInput, persist_completed_run_memory_node
from src.memory.reflection import MemoryReflectionInput, reflect_completed_run_memory_with_agents
from src.server.routes.chat.helpers import _set_skill_run_status, _sse_event
from src.server.routes.chat.models import ChatModelCacheRun, ChatSkillRun, ChatTeamActivity, ChatToolRun
from src.server.routes.chat.runtime_events import runtime_event_sse
from src.server.routes.chat.skill_runs import update_skill_run_status
from src.server.routes.chat.store import _append_assistant_message
from src.server.routes.chat.team_state import _finalize_team_activities, _set_team_activity_status
from src.server.routes.chat.trace_recorder import _record_run_event, _trim_text


def missing_final_answer_error() -> str:
    """生成没有最终答案时返回给前端的错误说明。"""

    return (
        "Agent 执行已停止，但没有产生最终答案。"
        "可能已达到最大 ReAct 迭代次数，或模型没有调用 FinalAnswerStart 后输出最终回复。"
    )


def max_react_iterations_error() -> str:
    """生成达到最大 ReAct 迭代次数时返回给前端的错误说明。"""

    return "Agent 已达到最大 ReAct 迭代次数，执行已停止。请缩小任务范围或提高最大迭代次数后重试。"


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
    sandbox: bool = False,
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
    yield runtime_event_sse(
        "RUN_FAILED",
        session_id=session_id,
        scope="run",
        agent_id=agent_id,
        agent_name=agent_name,
        status="failed",
        reason="startup_error",
        payload={"message": assistant_error},
    )
    if sandbox:
        yield runtime_event_sse(
            "SANDBOX_FAILED",
            session_id=session_id,
            scope="sandbox",
            agent_id=agent_id,
            agent_name=agent_name,
            status="failed",
            reason="startup_error",
            payload={"message": assistant_error},
        )
    if failed_skill_runs:
        for skill in failed_skill_runs:
            yield runtime_event_sse(
                "SKILL_FAILED",
                session_id=session_id,
                scope="skill",
                agent_id=agent_id,
                agent_name=agent_name,
                status="failed",
                payload=skill.model_dump(by_alias=True),
            )
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
    runtime_events: list[dict],
    skill_runs: list[ChatSkillRun],
    active_skill_run: dict | None,
    team_activities: list[ChatTeamActivity],
    team_id: str | None,
    team_name: str | None,
    sandbox: bool = False,
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
            runtime_events=runtime_events,
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
        _schedule_memory_reflection(
            MemoryReflectionInput(
                session_id=session_id,
                agent_id=agent_id,
                agent_name=agent_name,
                user_message=user_message,
                assistant_text=assistant_text,
            ),
            model_name=model_name,
        )
        try:
            persist_completed_run_memory_node(
                MemoryNodePersistInput(
                    session_id=session_id,
                    agent_id=agent_id,
                    agent_name=agent_name,
                    user_message=user_message,
                    assistant_text=assistant_text,
                ),
            )
        except Exception:
            pass
    if team_activities:
        yield runtime_event_sse(
            "TEAM_COMPLETED",
            session_id=session_id,
            scope="team",
            agent_id=agent_id,
            agent_name=agent_name,
            status="completed",
            payload={"activityCount": len(completed_team_activities)},
        )
        yield _sse_event("TEAM_ACTIVITY", {
            "activities": [
                item.model_dump(by_alias=True)
                for item in completed_team_activities
            ],
        })
    yield runtime_event_sse(
        "RUN_COMPLETED",
        session_id=session_id,
        scope="run",
        agent_id=agent_id,
        agent_name=agent_name,
        status="completed",
        payload={
            "model": model_name,
            "toolCallCount": len(assistant_tool_calls),
            "teamId": team_id,
            "teamName": team_name,
        },
    )
    if sandbox:
        yield runtime_event_sse(
            "SANDBOX_COMPLETED",
            session_id=session_id,
            scope="sandbox",
            agent_id=agent_id,
            agent_name=agent_name,
            status="completed",
            payload={
                "model": model_name,
                "toolCallCount": len(assistant_tool_calls),
                "teamId": team_id,
                "teamName": team_name,
            },
        )


def _schedule_memory_reflection(item: MemoryReflectionInput, *, model_name: str) -> None:
    """后台触发 Markdown 记忆提炼，不阻塞 Chat 成功收尾。"""

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(
            _run_memory_reflection_background(item, model_name=model_name),
            name=f"memory-reflection-{item.session_id}-{item.agent_id}",
        )
    except RuntimeError:
        # 极端情况下没有运行中的事件循环时，直接跳过后台记忆提炼，不能影响主流程。
        pass


async def _run_memory_reflection_background(item: MemoryReflectionInput, *, model_name: str) -> None:
    """后台运行三路记忆提炼 Agent，异常只记录为内部失败。"""

    try:
        await reflect_completed_run_memory_with_agents(item, model_name=model_name)
    except Exception:
        pass


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
    sandbox: bool = False,
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
        for skill in failed_skill_runs:
            yield runtime_event_sse(
                "SKILL_FAILED",
                session_id=session_id,
                scope="skill",
                agent_id=agent_id,
                agent_name=agent_name,
                status="failed",
                payload=skill.model_dump(by_alias=True),
            )
        yield _sse_event("SKILL_END", {
            "skills": [item.model_dump(by_alias=True) for item in failed_skill_runs],
            "status": "failed",
        })
    if team_activities:
        yield runtime_event_sse(
            "TEAM_FAILED",
            session_id=session_id,
            scope="team",
            agent_id=agent_id,
            agent_name=agent_name,
            status="failed",
            payload={"message": assistant_error},
        )
        yield _sse_event("TEAM_ACTIVITY", {
            "activities": [
                item.model_dump(by_alias=True)
                for item in _set_team_activity_status(team_activities, "failed")
            ],
        })
    yield runtime_event_sse(
        "RUN_FAILED",
        session_id=session_id,
        scope="run",
        agent_id=agent_id,
        agent_name=agent_name,
        status="failed",
        reason="agent_error",
        payload={"message": assistant_error},
    )
    if sandbox:
        yield runtime_event_sse(
            "SANDBOX_FAILED",
            session_id=session_id,
            scope="sandbox",
            agent_id=agent_id,
            agent_name=agent_name,
            status="failed",
            reason="agent_error",
            payload={"message": assistant_error},
        )
    yield _sse_event("ERROR", {
        "message": assistant_error,
    })
