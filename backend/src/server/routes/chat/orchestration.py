"""Chat 执行编排辅助。"""

from dataclasses import dataclass, field
from typing import AsyncGenerator

from src.server.routes.chat.helpers import _sse_event
from src.server.routes.chat.models import ChatMessage, ChatSkillRun, ChatTeamActivity
from src.server.routes.chat.prompts import _build_worker_outputs_system_prompt
from src.server.routes.chat.team import _run_team_workers
from src.server.routes.chat.team_state import (
    _collect_worker_outputs,
    _find_team_activity,
    _replace_team_activity,
)


@dataclass
class LeaderPreparationState:
    """Team worker 执行后 Leader 所需上下文。"""

    system_prompt: str
    worker_outputs: list[dict[str, str]] = field(default_factory=list)
    waiting: bool = False


async def prepare_leader_after_team_workers(
    *,
    state: LeaderPreparationState,
    data: ChatMessage,
    session_id: str,
    team_activities: list[ChatTeamActivity],
    model_name: str,
    leader_agent_id: str,
    skill_runs: list[ChatSkillRun],
    runtime_plan: dict | None = None,
) -> AsyncGenerator[str, None]:
    """执行 Team workers，并把 Worker 输出追加到 Leader system prompt。"""

    if not team_activities:
        return

    async for worker_event in _run_team_workers(
        data=data,
        session_id=session_id,
        team_activities=team_activities,
        model_name=model_name,
        leader_system_prompt=state.system_prompt,
        skill_runs=skill_runs,
        runtime_plan=runtime_plan,
    ):
        yield worker_event

    if any(activity.status == "waiting" for activity in team_activities):
        state.waiting = True
        return

    state.worker_outputs = _collect_worker_outputs(team_activities)
    if not state.worker_outputs:
        return

    state.system_prompt = "\n\n".join(
        part
        for part in [
            state.system_prompt,
            _build_worker_outputs_system_prompt(state.worker_outputs),
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
            "detail": f"Worker 已完成，Leader 正在基于 {len(state.worker_outputs)} 个 Worker 输出生成最终回复。",
            "leader_context_count": len(state.worker_outputs),
        }),
    )
    yield _sse_event("TEAM_ACTIVITY", {
        "activities": [item.model_dump(by_alias=True) for item in team_activities],
    })
