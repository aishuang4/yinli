"""Team 分派规划器。

该模块把 Team 模式的智能决策限定在“给已选 Worker 分配不同任务”这一层。
调度顺序仍由 team_scheduler 机械处理，Worker 执行仍由 team_worker_runtime 负责。
"""

import json
import re
from dataclasses import dataclass
from hashlib import sha256

from agentscope.message import UserMsg
from agentscope.permission import PermissionMode

from src.agents.factory import AgentGuardrails, AgentModelOptions, RuntimeToolScope, get_or_create_agent
from src.server.routes.chat.models import ChatTeamMemberPayload
from src.server.routes.chat.prompts import build_worker_assignment_text
from src.server.routes.team_defaults import DEFAULT_TEAM_ASSIGNMENT_PLANNER_PROMPT


@dataclass(frozen=True)
class TeamWorkerAssignment:
    """单个 Worker 的本轮 Leader 分派。"""

    agent_id: str
    task: str
    expected_output: str = ""
    constraints: str = ""
    depends_on: tuple[str, ...] = ()


async def plan_team_worker_assignments(
    *,
    message: str,
    team_name: str | None,
    team_members: list[ChatTeamMemberPayload],
    model_name: str,
) -> dict[str, TeamWorkerAssignment]:
    """为本轮参与的 Worker 生成结构化任务分派。

    模型输出不可用时回退到成员 handoff/description/name 推断，保证 Team Runtime
    不因为 planner 失败而中断。
    """

    workers = [
        member
        for member in team_members
        if member.role != "leader"
    ]
    if not workers:
        return {}

    try:
        assignments = await _plan_with_model(
            message=message,
            team_name=team_name,
            workers=workers,
            model_name=model_name,
        )
    except Exception:
        assignments = {}

    if set(assignments) >= {member.agent_id for member in workers}:
        return assignments
    return {
        **_fallback_assignments(workers),
        **assignments,
    }


async def _plan_with_model(
    *,
    message: str,
    team_name: str | None,
    workers: list[ChatTeamMemberPayload],
    model_name: str,
) -> dict[str, TeamWorkerAssignment]:
    """调用内置 planner Agent 生成分派 JSON。"""

    request_fingerprint = "\n".join([
        team_name or "",
        message,
        "\n".join(f"{member.agent_id}:{member.name}:{member.description}:{member.handoff_prompt}" for member in workers),
    ])
    prompt_hash = sha256(DEFAULT_TEAM_ASSIGNMENT_PLANNER_PROMPT.encode("utf-8")).hexdigest()[:12]
    request_hash = sha256(request_fingerprint.encode("utf-8")).hexdigest()[:12]
    agent = get_or_create_agent(
        agent_id=f"system:team-assignment-planner:{model_name}:prompt-{prompt_hash}:request-{request_hash}",
        name="TeamAssignmentPlanner",
        system_prompt=DEFAULT_TEAM_ASSIGNMENT_PLANNER_PROMPT,
        model_name=model_name,
        thinking_enable=False,
        permission_mode=PermissionMode.BYPASS,
        model_options=AgentModelOptions(max_tokens=2000, max_react_iters=2),
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
        "Original user task:",
        message,
        "Selected workers:",
        "\n".join(worker_lines),
    ])

    text = ""
    async for event in agent.reply_stream(UserMsg(name="user", content=prompt)):
        event_data = event.model_dump(mode="json")
        event_type = event_data.pop("type", "UNKNOWN")
        if event_type == "TEXT_BLOCK_DELTA":
            text += str(event_data.get("delta", ""))

    return parse_team_assignment_json(text, {member.agent_id for member in workers})


def parse_team_assignment_json(
    text: str,
    allowed_agent_ids: set[str],
) -> dict[str, TeamWorkerAssignment]:
    """解析并校验 planner JSON。"""

    raw = _extract_json_object(text)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        return {}

    items = data.get("assignments") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return {}

    assignments: dict[str, TeamWorkerAssignment] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        agent_id = str(item.get("agentId") or item.get("agent_id") or "").strip()
        if agent_id not in allowed_agent_ids or agent_id in assignments:
            continue
        task = str(item.get("task") or "").strip()
        if not task:
            continue
        depends_on = tuple(
            str(dep).strip()
            for dep in (item.get("dependsOn") or item.get("depends_on") or [])
            if str(dep).strip() in allowed_agent_ids
        )
        assignments[agent_id] = TeamWorkerAssignment(
            agent_id=agent_id,
            task=task[:1200],
            expected_output=str(item.get("expectedOutput") or item.get("expected_output") or "").strip()[:1200],
            constraints=str(item.get("constraints") or "").strip()[:1200],
            depends_on=depends_on,
        )
    return assignments


def _extract_json_object(text: str) -> str:
    """从模型文本中提取首个 JSON 对象。"""

    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?", "", raw, flags=re.IGNORECASE).strip()
        raw = re.sub(r"```$", "", raw).strip()
    if "{" not in raw or "}" not in raw:
        return ""
    return raw[raw.find("{"): raw.rfind("}") + 1]


def _fallback_assignments(
    workers: list[ChatTeamMemberPayload],
) -> dict[str, TeamWorkerAssignment]:
    """模型 planner 不可用时的保守兜底分派。"""

    return {
        member.agent_id: TeamWorkerAssignment(
            agent_id=member.agent_id,
            task=build_worker_assignment_text(member),
            expected_output="返回可供 Leader 汇总的 TeamSay-style report。",
            constraints="不要直接生成最终团队答案，只报告你的专业发现、证据和风险。",
            depends_on=tuple(member.depends_on),
        )
        for member in workers
    }
