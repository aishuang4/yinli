"""Team worker 预选端点 — 用 leader 模型挑选本轮相关 worker。"""

import uuid

from agentscope.message import UserMsg
from agentscope.permission import PermissionMode

from src.agents.factory import get_or_create_agent
from src.config.runtime_settings import get_effective_model_settings

from src.server.routes.chat.models import (
    TeamWorkerSelectionRequest,
    TeamWorkerSelectionResponse,
)
from src.server.routes.chat.prompts import _parse_worker_selection
from src.server.routes.chat.router import router
from src.server.routes.chat.team_state import _expand_selected_workers_with_dependencies


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
    agent = get_or_create_agent(
        agent_id=f"team-worker-selector:{uuid.uuid4().hex}",
        name="TeamWorkerSelector",
        system_prompt="\n".join([
            "You select which worker agents should participate in one Team run.",
            "Return only a compact JSON object. Do not call tools. Do not write markdown.",
            'Schema: {"selectedAgentIds":["agent-id"],"reason":"short reason"}',
            "Select only workers that are materially relevant to the user task.",
            "If the task is simple and no worker is needed, return an empty selectedAgentIds array.",
            "If a selected worker depends on another worker, include the dependency worker too.",
        ]),
        model_name=model_name,
        permission_mode=PermissionMode.BYPASS,
    )
    worker_lines = []
    for member in workers:
        dependencies = ", ".join(member.depends_on) if member.depends_on else "none"
        worker_lines.append(
            f"- agentId={member.agent_id}; name={member.name}; role={member.role}; "
            f"description={member.description or 'none'}; executionMode={member.execution_mode}; "
            f"dependsOn={dependencies}; handoffPrompt={member.handoff_prompt or 'none'}"
        )
    prompt = "\n\n".join([
        f"Team: {data.team_name or 'Unnamed Team'}",
        "User task:",
        data.message,
        "Available workers:",
        "\n".join(worker_lines),
    ])

    text = ""
    async for event in agent.reply_stream(UserMsg(name="user", content=prompt)):
        event_data = event.model_dump(mode="json")
        event_type = event_data.pop("type", "UNKNOWN")
        if event_type == "TEXT_BLOCK_DELTA":
            text += str(event_data.get("delta", ""))

    selected, reason = _parse_worker_selection(text, {member.agent_id for member in workers})
    selected = _expand_selected_workers_with_dependencies(selected, workers)
    return TeamWorkerSelectionResponse(selectedAgentIds=selected, reason=reason)
