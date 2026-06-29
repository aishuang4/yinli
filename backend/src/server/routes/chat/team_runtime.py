"""AgentScope-style Team Runtime 适配层。

AgentScope 2.0 的 Team 能力以 TeamCreate / AgentCreate / TeamSay 这类
工具为核心：Leader 创建成员，Worker 在独立 session 中执行，完成后通过
TeamSay 向 Leader 回报。

当前工作台已有自己的 Team 配置、SQLite 持久化和前端活动面板，因此本模块
先提供兼容适配层：保留现有产品数据结构，但把执行语义收束成
"Leader dispatch -> Worker isolated ReAct -> Worker report -> Leader synthesize"。
后续真正接入 AgentScope app storage/message_bus 时，只需要替换本模块内部。
"""

from dataclasses import dataclass, field
from typing import AsyncGenerator

from src.server.routes.chat.helpers import _sse_event
from src.server.routes.chat.models import ChatMessage, ChatSkillRun, ChatTeamActivity
from src.server.routes.chat.prompts import _build_worker_outputs_system_prompt
from src.server.routes.chat.runtime_events import runtime_event_sse
from src.server.routes.chat.team import _run_team_workers
from src.server.routes.chat.team_report_reviewer import review_team_worker_reports
from src.server.routes.chat.team_state import (
    _collect_worker_outputs,
    _find_team_activity,
    _replace_team_activity,
)


@dataclass
class TeamRuntimeState:
    """Team Runtime 交给 Leader 的执行结果。"""

    leader_system_prompt: str
    worker_reports: list[dict[str, str]] = field(default_factory=list)
    waiting: bool = False


async def prepare_leader_with_team_runtime(
    *,
    state: TeamRuntimeState,
    data: ChatMessage,
    session_id: str,
    team_activities: list[ChatTeamActivity],
    model_name: str,
    leader_agent_id: str,
    skill_runs: list[ChatSkillRun],
) -> AsyncGenerator[str, None]:
    """执行 Team Worker，并把 Worker report 注入 Leader 上下文。"""

    if not team_activities:
        return

    async for worker_event in _run_team_workers(
        data=data,
        session_id=session_id,
        team_activities=team_activities,
        model_name=model_name,
        leader_system_prompt=state.leader_system_prompt,
        skill_runs=skill_runs,
    ):
        yield worker_event

    if any(activity.status == "waiting" for activity in team_activities):
        state.waiting = True
        return

    reviews = await review_team_worker_reports(
        message=data.message,
        team_name=data.team_name,
        activities=team_activities,
        model_name=model_name,
    )
    for activity in list(team_activities):
        review = reviews.get(activity.agent_id)
        if not review:
            continue
        _replace_team_activity(
            team_activities,
            activity.model_copy(update={
                "report_review_status": review.status,
                "report_review_notes": review.notes,
                "report_review_gaps": list(review.gaps),
            }),
        )
    if reviews:
        yield runtime_event_sse(
            "TEAM_REPORT_REVIEWED",
            session_id=session_id,
            scope="team",
            status=(
                "completed"
                if all(review.status == "accepted" for review in reviews.values())
                else "waiting"
            ),
            payload={
                "acceptedCount": len([review for review in reviews.values() if review.status == "accepted"]),
                "needsRevisionCount": len([review for review in reviews.values() if review.status == "needs_revision"]),
            },
        )
        yield _sse_event("TEAM_ACTIVITY", {
            "activities": [item.model_dump(by_alias=True) for item in team_activities],
        })

    state.worker_reports = _collect_worker_outputs(team_activities)
    if not state.worker_reports:
        return

    state.leader_system_prompt = "\n\n".join(
        part
        for part in [
            state.leader_system_prompt,
            _build_worker_outputs_system_prompt(state.worker_reports),
        ]
        if part
    )
    leader_activity = _find_team_activity(team_activities, leader_agent_id)
    if not leader_activity:
        return

    _replace_team_activity(
        team_activities,
        leader_activity.model_copy(update={
            "status": "running",
            "detail": f"已收到 {len(state.worker_reports)} 个 Worker report，Leader 正在汇总最终回复。",
            "leader_context_count": len(state.worker_reports),
        }),
    )
    yield _sse_event("TEAM_ACTIVITY", {
        "activities": [item.model_dump(by_alias=True) for item in team_activities],
    })
