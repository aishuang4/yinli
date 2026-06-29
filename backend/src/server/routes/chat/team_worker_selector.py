"""Team Runtime 的 Worker 选择器。

Worker 裁剪属于 Team 执行策略，不属于第一层 Intent Router。
"""

from hashlib import sha256

from agentscope.message import UserMsg
from agentscope.permission import PermissionMode

from src.agents.factory import AgentGuardrails, AgentModelOptions, RuntimeToolScope, get_or_create_agent
from src.server.routes.chat.models import ChatTeamMemberPayload
from src.server.routes.chat.prompts import _parse_worker_selection
from src.server.routes.chat.team_state import _expand_selected_workers_with_dependencies
from src.server.routes.team_defaults import DEFAULT_WORKER_SELECTOR_PROMPT


WORKER_SELECTION_PARSE_FAILED_REASON = "模型未返回有效选择，保持人工选择。"


def filter_team_members_for_runtime(
    *,
    members: list[ChatTeamMemberPayload],
    selected_member_ids: list[str],
    leader_agent_id: str,
    keep_all_when_empty: bool = True,
) -> list[ChatTeamMemberPayload]:
    """按 Runtime 选择结果裁剪 Team 成员。"""

    if not members:
        return members
    if not selected_member_ids and keep_all_when_empty:
        return members
    selected = set(selected_member_ids)
    return [
        member
        for member in members
        if member.role == "leader"
        or member.agent_id == leader_agent_id
        or member.agent_id in selected
    ]


async def select_team_workers_for_route(
    *,
    message: str,
    agent_id: str,
    team_name: str | None,
    team_members: list[ChatTeamMemberPayload],
    model_name: str,
    selector_prompt: str = "",
) -> tuple[list[str], str]:
    """用模型从 Team 中选择本轮相关 Worker，返回已展开依赖的 agent_id 列表。"""

    workers = [
        member
        for member in team_members
        if member.role != "leader" and member.agent_id != agent_id
    ]
    if not workers:
        return [], "当前 Team 没有可选择的 Worker。"

    effective_prompt = selector_prompt.strip() or DEFAULT_WORKER_SELECTOR_PROMPT
    prompt_hash = sha256(effective_prompt.encode("utf-8")).hexdigest()[:12]
    agent = get_or_create_agent(
        agent_id=f"system:team-worker-selector:{model_name}:prompt-{prompt_hash}",
        name="TeamWorkerSelector",
        system_prompt=effective_prompt,
        model_name=model_name,
        thinking_enable=False,
        permission_mode=PermissionMode.BYPASS,
        model_options=AgentModelOptions(max_tokens=1000, max_react_iters=2),
        guardrails=AgentGuardrails(
            network_enabled=False,
            tool_permission_profile="read_only",
        ),
        tool_scope=RuntimeToolScope(
            allow_mcp=False,
            allow_knowledge=False,
            allow_bash=False,
            allow_http=False,
        ),
        include_default_tools=False,
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
        f"Team: {team_name or 'Unnamed Team'}",
        "User task:",
        message,
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
    if reason == WORKER_SELECTION_PARSE_FAILED_REASON:
        return [], WORKER_SELECTION_PARSE_FAILED_REASON
    return _expand_selected_workers_with_dependencies(selected, workers), reason
