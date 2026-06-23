"""Chat 前置意图路由。

该模块只做轻量决策，不创建 Agent、不消费 SSE。主链路根据 IntentRoute
决定本轮要启用的 Plan、Skill、Subagent 和后续编排能力。
"""

import re
import uuid
from typing import Any, Literal

from agentscope.message import UserMsg
from agentscope.permission import PermissionMode
from pydantic import BaseModel, Field

from src.agents.factory import get_or_create_agent
from src.server.routes.chat.models import ChatImagePayload, ChatTeamMemberPayload
from src.server.routes.chat.plans import detect_plan_intent
from src.server.routes.chat.prompts import _parse_worker_selection
from src.server.routes.chat.team_state import _expand_selected_workers_with_dependencies
from src.server.routes.skills import select_relevant_skills


RouteMode = Literal[
    "conversation",
    "clarification",
    "direct_answer",
    "agent_execution",
    "planned_execution",
    "team_execution",
    "subagent_execution",
    "skill_execution",
]


class RouteDecision(BaseModel):
    """单项路由决策及原因。"""

    name: str
    enabled: bool
    confidence: float = 1.0
    reason: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


class ToolScope(BaseModel):
    """本轮需要注入给主 Agent 的工具范围。"""

    allow_mcp: bool = Field(default=False, alias="allowMcp")
    allow_knowledge: bool = Field(default=False, alias="allowKnowledge")
    allow_bash: bool = Field(default=False, alias="allowBash")
    allow_http: bool = Field(default=False, alias="allowHttp")
    reason: str = ""

    model_config = {"populate_by_name": True}


class IntentRoute(BaseModel):
    """本轮 Chat 运行的前置路由结果。"""

    mode: RouteMode = "conversation"
    decisions: list[RouteDecision] = Field(default_factory=list)
    selected_skill_ids: list[str] = Field(default_factory=list, alias="selectedSkillIds")
    selected_subagent_ids: list[str] = Field(default_factory=list, alias="selectedSubagentIds")
    selected_team_member_ids: list[str] = Field(default_factory=list, alias="selectedTeamMemberIds")
    should_create_plan: bool = Field(default=False, alias="shouldCreatePlan")
    require_clarification: bool = Field(default=False, alias="requireClarification")
    clarification: dict[str, Any] | None = None
    tool_scope: ToolScope = Field(default_factory=ToolScope, alias="toolScope")
    main_agent_instruction: str = Field(default="", alias="mainAgentInstruction")

    model_config = {"populate_by_name": True}


def route_chat_message(
    *,
    message: str,
    agent_config: object | None,
    skill_ids: list[str],
    team_members: list[ChatTeamMemberPayload],
    images: list[ChatImagePayload],
) -> IntentRoute:
    """生成本轮 Chat 的前置路由结果。"""

    decisions: list[RouteDecision] = []
    selected_skills = select_relevant_skills(skill_ids, message)
    selected_skill_ids = [skill.id for skill in selected_skills]
    decisions.append(RouteDecision(
        name="skill",
        enabled=bool(selected_skill_ids),
        confidence=0.9 if selected_skill_ids else 0.7,
        reason=(
            f"匹配到 Skill: {', '.join(skill.name for skill in selected_skills)}"
            if selected_skills
            else "未匹配到本轮需要加载的 Skill。"
        ),
        payload={"selectedSkillIds": selected_skill_ids},
    ))

    available_subagents = _callable_agent_summaries(agent_config)
    selected_subagent_ids = _select_subagent_ids(message, available_subagents)
    decisions.append(RouteDecision(
        name="subagent",
        enabled=bool(selected_subagent_ids),
        confidence=0.82 if selected_subagent_ids else 0.65,
        reason=(
            f"匹配到可委派子 Agent: {', '.join(selected_subagent_ids)}"
            if selected_subagent_ids
            else "未匹配到需要预注入的子 Agent。"
        ),
        payload={
            "availableSubagentIds": [item["id"] for item in available_subagents],
            "selectedSubagentIds": selected_subagent_ids,
        },
    ))

    selected_team_member_ids = _select_team_member_ids(message, team_members)
    has_team = bool(team_members)
    decisions.append(RouteDecision(
        name="team",
        enabled=has_team,
        confidence=0.8 if selected_team_member_ids else 0.6,
        reason=(
            f"Team 模式，本轮候选 Worker: {', '.join(selected_team_member_ids)}"
            if selected_team_member_ids
            else ("Team 模式但未做 Worker 裁剪。" if has_team else "非 Team 模式。")
        ),
        payload={"selectedTeamMemberIds": selected_team_member_ids},
    ))

    clarification = _detect_missing_required_context(message)
    has_runtime_tools = _has_runtime_tools(agent_config) or bool(selected_subagent_ids)
    should_plan = not clarification and detect_plan_intent(
        message=message,
        has_team=has_team,
        has_tools=has_runtime_tools,
        has_skills=bool(selected_skill_ids),
        has_images=bool(images),
    )
    plan_config = getattr(agent_config, "plan_config", None)
    should_plan = should_plan and bool(getattr(plan_config, "enabled", True))
    decisions.append(RouteDecision(
        name="plan",
        enabled=should_plan,
        confidence=0.9 if should_plan else 0.7,
        reason=(
            "识别到规划/多步执行意图。"
            if should_plan
            else ("等待补充信息，暂不创建 Plan Runtime。" if clarification else "未识别到需要 Plan Runtime 的意图。")
        ),
        payload={"planEnabled": bool(getattr(plan_config, "enabled", True))},
    ))

    decisions.append(RouteDecision(
        name="clarification",
        enabled=clarification is not None,
        confidence=0.75 if clarification else 0.6,
        reason="执行前缺少必要目标信息。" if clarification else "未发现必须前置追问的信息缺口。",
        payload=clarification or {},
    ))

    tool_scope = _detect_tool_scope(
        message=message,
        agent_config=agent_config,
        has_selected_subagents=bool(selected_subagent_ids),
        has_selected_skills=bool(selected_skill_ids),
        has_team=has_team,
        has_images=bool(images),
    )
    decisions.append(RouteDecision(
        name="tool_scope",
        enabled=tool_scope.allow_mcp or tool_scope.allow_knowledge or tool_scope.allow_bash or tool_scope.allow_http,
        confidence=0.82,
        reason=tool_scope.reason or "本轮仅保留基础本地读取和人工输入工具。",
        payload=tool_scope.model_dump(by_alias=True),
    ))

    mode = _route_mode(
        has_team=has_team,
        should_plan=should_plan,
        selected_skill_ids=selected_skill_ids,
        selected_subagent_ids=selected_subagent_ids,
        clarification=clarification,
        message=message,
        images=images,
    )
    return IntentRoute(
        mode=mode,
        decisions=decisions,
        selectedSkillIds=selected_skill_ids,
        selectedSubagentIds=selected_subagent_ids,
        selectedTeamMemberIds=selected_team_member_ids,
        shouldCreatePlan=should_plan,
        requireClarification=clarification is not None,
        clarification=clarification,
        toolScope=tool_scope,
        mainAgentInstruction=_main_agent_instruction(mode),
    )


def route_trace_payload(route: IntentRoute) -> dict[str, Any]:
    """生成可写入 run_events 的稳定 payload。"""

    data = route.model_dump(by_alias=True)
    data["decisions"] = [decision.model_dump() for decision in route.decisions]
    return data


async def refine_team_route_with_model(
    route: IntentRoute,
    *,
    message: str,
    agent_id: str,
    team_name: str | None,
    team_members: list[ChatTeamMemberPayload],
    model_name: str,
) -> IntentRoute:
    """当规则层未选中 Team worker 时，用轻量模型选择本轮 Worker。"""

    if not team_members or route.selected_team_member_ids:
        return route
    selected, reason = await select_team_workers_for_route(
        message=message,
        agent_id=agent_id,
        team_name=team_name,
        team_members=team_members,
        model_name=model_name,
    )
    if not selected:
        return route

    decisions = [
        (
            decision.model_copy(update={
                "enabled": True,
                "confidence": 0.82,
                "reason": reason or f"模型路由选择 Worker: {', '.join(selected)}",
                "payload": {
                    **decision.payload,
                    "selectedTeamMemberIds": selected,
                    "selector": "model",
                },
            })
            if decision.name == "team"
            else decision
        )
        for decision in route.decisions
    ]
    mode: RouteMode = "team_execution"
    return route.model_copy(update={
        "mode": mode,
        "decisions": decisions,
        "selected_team_member_ids": selected,
        "main_agent_instruction": _main_agent_instruction(mode),
    })


async def select_team_workers_for_route(
    *,
    message: str,
    agent_id: str,
    team_name: str | None,
    team_members: list[ChatTeamMemberPayload],
    model_name: str,
) -> tuple[list[str], str]:
    """用模型从 Team 中选择本轮相关 Worker，返回已展开依赖的 agent_id 列表。"""

    workers = [
        member
        for member in team_members
        if member.role != "leader" and member.agent_id != agent_id
    ]
    if not workers:
        return [], "当前 Team 没有可选择的 Worker。"

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
    return _expand_selected_workers_with_dependencies(selected, workers), reason


def filter_team_members_for_route(
    members: list[ChatTeamMemberPayload],
    selected_member_ids: list[str],
    leader_agent_id: str,
) -> list[ChatTeamMemberPayload]:
    """按路由结果裁剪 Team 成员；未选中任何 Worker 时保持原列表。"""

    if not members or not selected_member_ids:
        return members
    selected = set(selected_member_ids)
    return [
        member
        for member in members
        if member.role == "leader"
        or member.agent_id == leader_agent_id
        or member.agent_id in selected
    ]


def _callable_agent_summaries(agent_config: object | None) -> list[dict[str, str]]:
    if not agent_config:
        return []
    from src.server.routes.agents import get_registered_agent

    summaries: list[dict[str, str]] = []
    for agent_id in list(getattr(agent_config, "callable_agents", []) or []):
        child = get_registered_agent(str(agent_id))
        if not child:
            continue
        summaries.append({
            "id": str(child.id),
            "name": str(child.name),
            "description": str(child.description or ""),
            "systemPrompt": str(getattr(child, "system_prompt", "") or ""),
        })
    return summaries


def _select_subagent_ids(message: str, subagents: list[dict[str, str]]) -> list[str]:
    if not subagents:
        return []

    normalized = message.strip().lower()
    explicit_delegate = bool(re.search(r"(子agent|子 agent|subagent|委派|交给|调用.*agent|让.*agent)", normalized))
    scored: list[tuple[int, str]] = []
    for item in subagents:
        score = 0
        for field in (item["id"], item["name"], item["description"]):
            value = field.strip().lower()
            if value and value in normalized:
                score += 4
        for token in _keyword_tokens(item["name"], item["description"]):
            if token in normalized:
                score += 1
        if score > 0:
            scored.append((score, item["id"]))

    if scored:
        scored.sort(key=lambda item: item[0], reverse=True)
        top_score = scored[0][0]
        return [agent_id for score, agent_id in scored if score == top_score or score >= 4][:3]

    if explicit_delegate and len(subagents) == 1:
        return [subagents[0]["id"]]
    return []


def _select_team_member_ids(message: str, members: list[ChatTeamMemberPayload]) -> list[str]:
    workers = [member for member in members if member.role != "leader"]
    if not workers:
        return []
    normalized = message.strip().lower()
    selected: list[str] = []
    for member in workers:
        fields = [member.agent_id, member.name, member.role, member.description, member.handoff_prompt]
        tokens = _keyword_tokens(*(field or "" for field in fields))
        if any(token in normalized for token in tokens):
            selected.append(member.agent_id)
    return selected[:6]


def _has_runtime_tools(agent_config: object | None) -> bool:
    if not agent_config:
        return False
    return bool(
        list(getattr(agent_config, "mcp_tools", []) or [])
        or list(getattr(agent_config, "knowledge_tools", []) or [])
        or list(getattr(agent_config, "callable_agents", []) or [])
    )


def _detect_tool_scope(
    *,
    message: str,
    agent_config: object | None,
    has_selected_subagents: bool,
    has_selected_skills: bool,
    has_team: bool,
    has_images: bool,
) -> ToolScope:
    text = message.strip().lower()
    configured_mcp = bool(list(getattr(agent_config, "mcp_tools", []) or []))
    configured_knowledge = bool(list(getattr(agent_config, "knowledge_tools", []) or []))
    needs_network = bool(re.search(r"(http|https|接口|api|网页|网站|联网|搜索|调研|资料|下载|请求|curl|url|链接|最新|今天|新闻|价格|汇率)", text))
    needs_shell = bool(re.search(r"(运行|执行|测试|构建|启动|命令|脚本|pytest|npm|uv|bash|shell|终端|部署)", text))
    needs_knowledge = bool(re.search(r"(知识库|资料库|文档库|检索|搜索|查询|调研|资料|上下文|文档)", text))
    needs_mcp = bool(re.search(r"(mcp|外部工具|数据库|浏览器|github|figma|notion|jira|linear|slack)", text))

    allow_knowledge = configured_knowledge and (needs_knowledge or needs_network or has_selected_skills or has_team)
    allow_mcp = configured_mcp and (needs_mcp or needs_network or has_selected_subagents or has_team)
    allow_bash = needs_shell
    allow_http = needs_network

    reasons: list[str] = []
    if allow_knowledge:
        reasons.append("匹配到知识库/资料检索意图")
    if allow_mcp:
        reasons.append("匹配到 MCP/外部工具意图")
    if allow_bash:
        reasons.append("匹配到命令执行意图")
    if allow_http:
        reasons.append("匹配到 HTTP/网络请求意图")
    if has_images and not reasons:
        reasons.append("图片任务保留基础多模态处理，不额外启用网络工具")
    return ToolScope(
        allowMcp=allow_mcp,
        allowKnowledge=allow_knowledge,
        allowBash=allow_bash,
        allowHttp=allow_http,
        reason="；".join(reasons),
    )


def _detect_missing_required_context(message: str) -> dict[str, Any] | None:
    text = message.strip()
    if not text:
        return {
            "title": "需要补充任务内容",
            "description": "当前消息为空，无法判断要执行的任务。",
            "questions": [{"id": "task", "type": "text", "label": "请补充要处理的任务"}],
        }
    if re.search(r"(修改|修复|读取|检查|运行|删除|重命名)", text) and not re.search(r"(/|\\|\.py|\.ts|\.tsx|\.md|文件|目录|项目|代码|接口|页面|组件)", text):
        return {
            "title": "需要补充目标对象",
            "description": "该请求像执行型任务，但缺少明确的文件、目录、项目或对象。",
            "questions": [{"id": "target", "type": "text", "label": "要处理的目标是什么？"}],
        }
    return None


def _route_mode(
    *,
    has_team: bool,
    should_plan: bool,
    selected_skill_ids: list[str],
    selected_subagent_ids: list[str],
    clarification: dict[str, Any] | None,
    message: str,
    images: list[ChatImagePayload],
) -> RouteMode:
    if clarification:
        return "clarification"
    if has_team:
        return "team_execution"
    if selected_subagent_ids:
        return "subagent_execution"
    if should_plan:
        return "planned_execution"
    if selected_skill_ids:
        return "skill_execution"
    if images or re.search(r"(创建|修改|实现|生成|分析|检查|测试|修复|运行|读取|查找|总结|写)", message):
        return "agent_execution"
    return "conversation"


def _main_agent_instruction(mode: RouteMode) -> str:
    if mode == "conversation":
        return "本轮按普通对话处理，不要主动输出计划或调用子 Agent。"
    if mode == "planned_execution":
        return "本轮已识别为多步执行任务，请遵循 Plan Runtime。"
    if mode == "subagent_execution":
        return "本轮只注入匹配到的子 Agent，需要委派时优先使用它们。"
    if mode == "skill_execution":
        return "本轮只加载匹配到的 Skills，并按 Skill 手册执行。"
    if mode == "team_execution":
        return "本轮是 Team 协作任务，Leader 应基于 Worker 输出汇总。"
    if mode == "clarification":
        return "本轮存在信息缺口，优先补充必要信息后再执行。"
    return ""


def _keyword_tokens(*values: str) -> set[str]:
    tokens: set[str] = set()
    for value in values:
        for token in re.findall(r"[a-z0-9\u4e00-\u9fff]{2,}", value.lower()):
            tokens.add(token)
    return tokens
