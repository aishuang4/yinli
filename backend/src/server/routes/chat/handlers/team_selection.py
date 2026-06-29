"""Team worker 预选端点 — 用 leader 模型挑选本轮相关 worker。"""

from src.config.runtime_settings import get_effective_model_settings

from src.server.routes.chat.models import (
    TeamWorkerSelectionRequest,
    TeamWorkerSelectionResponse,
)
from src.server.routes.chat.router import router
from src.server.routes.chat.team_worker_selector import select_team_workers_for_route


@router.post("/team-worker-selection", response_model=TeamWorkerSelectionResponse)
async def select_team_workers(data: TeamWorkerSelectionRequest) -> TeamWorkerSelectionResponse:
    """Use the leader model to preselect relevant workers for a single Team run."""

    workers = [
        member
        for member in data.team_members
        if member.role != "leader" and member.agent_id != data.agent_id
    ]
    if not workers:
        return TeamWorkerSelectionResponse(selectedAgentIds=[], reason="当前 Team 没有可选择的 Worker。")

    runtime_model_settings = get_effective_model_settings()
    model_name = data.model or runtime_model_settings.default_model
    selected, reason = await select_team_workers_for_route(
        message=data.message,
        agent_id=data.agent_id or "",
        team_name=data.team_name,
        team_members=data.team_members,
        model_name=model_name,
        selector_prompt=data.worker_selector_prompt,
    )
    return TeamWorkerSelectionResponse(selectedAgentIds=selected, reason=reason)
