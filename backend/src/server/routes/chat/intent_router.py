"""Chat 第一层运行线路路由。

该模块只判断本轮应该进入哪条运行线路：ReAct、Team 或前置追问。
Skill、工具、Worker 裁剪暂时保留为兼容 hints，后续迁入具体 runtime。
"""

import json
import re
import uuid
from typing import Any, Literal

from agentscope.message import UserMsg
from agentscope.permission import PermissionMode
from pydantic import BaseModel, Field

from src.agents.factory import AgentGuardrails, AgentModelOptions, RuntimeToolScope, get_or_create_agent
from src.server.routes.chat.models import ChatImagePayload, ChatTeamMemberPayload
from src.server.routes.chat.prompts import _parse_worker_selection
from src.server.routes.chat.team_state import _expand_selected_workers_with_dependencies
from src.server.routes.skills import get_installed_skill, select_relevant_skills


RuntimeKind = Literal["react", "team", "clarification"]


class RuntimeRoute(BaseModel):
    """第一层运行线路选择，后续可通过 config 扩展更多线路。"""

    kind: RuntimeKind = "react"
    confidence: float = 1.0
    reason: str = ""
    config: dict[str, Any] = Field(default_factory=dict)


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

    mode: RuntimeKind = "react"
    runtime: RuntimeRoute = Field(default_factory=RuntimeRoute)
    decisions: list[RouteDecision] = Field(default_factory=list)
    selected_skill_ids: list[str] = Field(default_factory=list, alias="selectedSkillIds")
    selected_team_member_ids: list[str] = Field(default_factory=list, alias="selectedTeamMemberIds")
    require_clarification: bool = Field(default=False, alias="requireClarification")
    clarification: dict[str, Any] | None = None
    tool_scope: ToolScope = Field(default_factory=ToolScope, alias="toolScope")
    main_agent_instruction: str = Field(default="", alias="mainAgentInstruction")

    model_config = {"populate_by_name": True}


class ModelIntentRoutePayload(BaseModel):
    """模型第一层路由返回的结构化结果。"""

    runtime: RuntimeRoute = Field(default_factory=RuntimeRoute)
    intent: str = "react"
    confidence: float = 0.5
    reason: str = ""
    require_clarification: bool = Field(default=False, alias="requireClarification")
    clarification: dict[str, Any] | None = None

    model_config = {"populate_by_name": True, "extra": "ignore"}


INTENT_CATALOG: list[dict[str, Any]] = [
    {
        "name": "react",
        "description": "默认单 Agent ReAct 线路，适合普通问答、工具调用、Skills 执行和单 Agent 任务。",
        "runtimeKind": "react",
    },
    {
        "name": "clarification",
        "description": "执行前缺少关键目标、范围或参数，需要先向用户追问。",
        "runtimeKind": "clarification",
    },
    {
        "name": "team",
        "description": "用户已选择 Team 或明确需要 Team Leader/Worker 协作。",
        "runtimeKind": "team",
    },
]


async def route_chat_message_with_model(
    *,
    message: str,
    agent_config: object | None,
    skill_ids: list[str],
    team_members: list[ChatTeamMemberPayload],
    images: list[ChatImagePayload],
    model_name: str,
    fallback_names: list[str] | None = None,
    team_name: str | None = None,
) -> IntentRoute:
    """模型优先的 Chat 前置路由，失败时回退到规则路由。

    模型只负责语义选择；最终可用能力仍由后端基于 Agent 配置和权限做裁剪。
    """

    fallback_route = route_chat_message(
        message=message,
        agent_config=agent_config,
        skill_ids=skill_ids,
        team_members=team_members,
        images=images,
    )
    if not message.strip():
        return fallback_route

    model_payload = await _run_model_intent_router(
        message=message,
        agent_config=agent_config,
        skill_ids=skill_ids,
        team_members=team_members,
        images=images,
        model_name=model_name,
        fallback_names=fallback_names or [],
    )
    if model_payload is None:
        return await _fallback_route_with_optional_team_refinement(
            fallback_route,
            message=message,
            agent_id=str(getattr(agent_config, "id", "") or ""),
            team_name=team_name,
            team_members=team_members,
            model_name=model_name,
        )

    return _sanitize_model_route(
        payload=model_payload,
        fallback_route=fallback_route,
        message=message,
        agent_config=agent_config,
        skill_ids=skill_ids,
        team_members=team_members,
        images=images,
    )


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

    runtime = _route_runtime(
        has_team=has_team,
        clarification=clarification,
    )
    decisions.insert(0, RouteDecision(
        name="runtime_route",
        enabled=True,
        confidence=runtime.confidence,
        reason=runtime.reason,
        payload=runtime.model_dump(),
    ))
    return IntentRoute(
        mode=runtime.kind,
        runtime=runtime,
        decisions=decisions,
        selectedSkillIds=selected_skill_ids,
        selectedTeamMemberIds=selected_team_member_ids,
        requireClarification=clarification is not None,
        clarification=clarification,
        toolScope=tool_scope,
        mainAgentInstruction=_main_agent_instruction(runtime.kind),
    )


def route_trace_payload(route: IntentRoute) -> dict[str, Any]:
    """生成可写入 run_events 的稳定 payload。"""

    data = route.model_dump(by_alias=True)
    data["decisions"] = [decision.model_dump() for decision in route.decisions]
    return data


async def _run_model_intent_router(
    *,
    message: str,
    agent_config: object | None,
    skill_ids: list[str],
    team_members: list[ChatTeamMemberPayload],
    images: list[ChatImagePayload],
    model_name: str,
    fallback_names: list[str],
) -> ModelIntentRoutePayload | None:
    """调用轻量模型完成本轮意图识别。"""

    try:
        router_agent_id = _intent_router_agent_id(model_name, fallback_names)
        agent = get_or_create_agent(
            agent_id=router_agent_id,
            name="IntentRouter",
            system_prompt=_build_model_router_system_prompt(),
            model_name=model_name,
            fallback_names=fallback_names,
            thinking_enable=False,
            permission_mode=PermissionMode.BYPASS,
            model_options=AgentModelOptions(max_tokens=1400, max_react_iters=2),
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
        )
        text = ""
        async for event in agent.reply_stream(UserMsg(name="user", content=_build_model_router_user_prompt(
            message=message,
            agent_config=agent_config,
            skill_ids=skill_ids,
            team_members=team_members,
            images=images,
        ))):
            event_data = event.model_dump(mode="json")
            if event_data.get("type") == "TEXT_BLOCK_DELTA":
                text += str(event_data.get("delta") or "")
        data = _extract_json_object(text)
        if not data:
            return None
        return ModelIntentRoutePayload.model_validate(data)
    except Exception:
        return None


def _intent_router_agent_id(model_name: str, fallback_names: list[str]) -> str:
    """生成固定的系统意图识别 Agent 缓存键。"""

    fallback_key = ",".join(fallback_names) if fallback_names else "none"
    return f"system:intent-router:{model_name}:fallbacks-{fallback_key}"


def _build_model_router_system_prompt() -> str:
    """构建模型意图路由器系统提示词。"""

    return "\n".join([
        "You are the intent router for an Agent workstation.",
        "Return only one compact valid JSON object. Do not call tools. Do not write markdown.",
        "Your only job is to select the first-layer runtime route.",
        "Current supported runtime kinds are: react, team, clarification.",
        "Do not select tools, skills, workers, or permissions.",
        "If the task lacks required target details, set requireClarification=true and provide concise questions.",
        "If teamContext.enabled is true and clarification is not required, prefer runtime.kind=team.",
        "Otherwise prefer runtime.kind=react.",
        "Schema:",
        json.dumps(
            {
                "runtime": {
                    "kind": "react | team | clarification",
                    "confidence": 0.0,
                    "reason": "short Chinese reason",
                    "config": {},
                },
                "intent": "react | team | clarification",
                "confidence": 0.0,
                "reason": "short Chinese reason",
                "requireClarification": False,
                "clarification": {
                    "title": "short title",
                    "description": "short description",
                    "questions": [{"id": "target", "type": "text | choice", "label": "question"}],
                },
            },
            ensure_ascii=False,
        ),
    ])


def _build_model_router_user_prompt(
    *,
    message: str,
    agent_config: object | None,
    skill_ids: list[str],
    team_members: list[ChatTeamMemberPayload],
    images: list[ChatImagePayload],
) -> str:
    """构建模型路由器用户提示词。"""

    skills = _skill_summaries(skill_ids)
    team_lines = [
        {
            "agentId": member.agent_id,
            "name": member.name,
            "role": member.role,
            "description": member.description or "",
            "executionMode": member.execution_mode,
            "dependsOn": member.depends_on,
            "handoffPrompt": member.handoff_prompt or "",
        }
        for member in team_members
    ]
    agent_summary = {
        "id": str(getattr(agent_config, "id", "") or ""),
        "name": str(getattr(agent_config, "name", "") or ""),
        "description": str(getattr(agent_config, "description", "") or ""),
        "configuredMcpToolCount": len(list(getattr(agent_config, "mcp_tools", []) or [])),
        "configuredKnowledgeToolCount": len(list(getattr(agent_config, "knowledge_tools", []) or [])),
        "configuredCallableAgentCount": 0,
    }
    payload = {
        "intentCatalog": INTENT_CATALOG,
        "userMessage": message,
        "agent": agent_summary,
        "hasImages": bool(images),
        "teamContext": {
            "enabled": bool(team_members),
            "name": "selected team" if team_members else "",
            "memberCount": len(team_members),
        },
        "routingPolicy": {
            "supportedRuntimeKinds": ["react", "team", "clarification"],
            "skillsAreSelectedInsideRuntime": True,
            "toolsAreSelectedInsideRuntime": True,
            "workersAreSelectedInsideTeamRuntime": True,
        },
    }
    if skills:
        payload["compatibilitySkillHints"] = skills
    if team_lines:
        payload["compatibilityTeamMemberHints"] = team_lines
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _sanitize_model_route(
    *,
    payload: ModelIntentRoutePayload,
    fallback_route: IntentRoute,
    message: str,
    agent_config: object | None,
    skill_ids: list[str],
    team_members: list[ChatTeamMemberPayload],
    images: list[ChatImagePayload],
) -> IntentRoute:
    """后端裁剪模型第一层路由，确保只进入当前支持的 runtime。"""

    available_skill_ids = {
        skill_id
        for skill_id in skill_ids
        if get_installed_skill(skill_id) is not None
    }
    # Skill/工具选择后续会迁入 ReAct runtime。当前保留规则 hints，避免存量功能中断。
    selected_skill_ids = [
        skill.id
        for skill in select_relevant_skills(skill_ids, message)
        if skill.id in available_skill_ids
    ]

    selected_team_member_ids = _select_team_member_ids(message, team_members)
    if selected_team_member_ids:
        selected_team_member_ids = _expand_selected_workers_with_dependencies(selected_team_member_ids, team_members)

    clarification = _normalize_clarification(payload.clarification) if payload.require_clarification else None
    if payload.require_clarification and clarification is None:
        clarification = fallback_route.clarification or {
            "title": "需要补充信息",
            "description": payload.reason or "当前任务需要补充关键信息后再执行。",
            "questions": [{"id": "details", "type": "text", "label": "请补充需要处理的目标、范围或约束"}],
        }

    tool_scope = _detect_tool_scope(
        message=message,
        agent_config=agent_config,
        has_selected_skills=bool(selected_skill_ids),
        has_team=bool(team_members),
        has_images=bool(images),
    )
    runtime = _sanitize_runtime_route(
        requested=payload.runtime,
        fallback=fallback_route.runtime,
        payload_confidence=payload.confidence,
        payload_reason=payload.reason,
        has_team=bool(team_members),
        clarification=clarification,
    )
    decisions = [
        RouteDecision(
            name="runtime_route",
            enabled=True,
            confidence=max(0.0, min(float(payload.confidence), 1.0)),
            reason=runtime.reason or payload.reason or "模型完成第一层运行线路识别。",
            payload={
                "intent": payload.intent,
                "requestedRuntime": payload.runtime.model_dump(),
                "sanitizedRuntime": runtime.model_dump(),
            },
        ),
        RouteDecision(
            name="skill",
            enabled=bool(selected_skill_ids),
            confidence=0.86 if selected_skill_ids else 0.72,
            reason="模型选择了本轮相关 Skill。" if selected_skill_ids else "模型未选择 Skill。",
            payload={"selectedSkillIds": selected_skill_ids},
        ),
        RouteDecision(
            name="team",
            enabled=bool(team_members),
            confidence=0.86 if selected_team_member_ids else 0.72,
            reason=(
                "模型选择了本轮相关 Worker。"
                if selected_team_member_ids
                else ("Team 模式下模型未裁剪 Worker。" if team_members else "非 Team 模式。")
            ),
            payload={"selectedTeamMemberIds": selected_team_member_ids, "selector": "model"},
        ),
        RouteDecision(
            name="clarification",
            enabled=clarification is not None,
            confidence=0.84 if clarification else 0.72,
            reason="模型判断执行前需要补充信息。" if clarification else "模型未发现必须前置追问的信息缺口。",
            payload=clarification or {},
        ),
        RouteDecision(
            name="tool_scope",
            enabled=tool_scope.allow_mcp or tool_scope.allow_knowledge or tool_scope.allow_bash or tool_scope.allow_http,
            confidence=0.84,
            reason=tool_scope.reason or "本轮仅保留基础本地读取和人工输入工具。",
            payload=tool_scope.model_dump(by_alias=True),
        ),
    ]
    return IntentRoute(
        mode=runtime.kind,
        runtime=runtime,
        decisions=decisions,
        selectedSkillIds=selected_skill_ids,
        selectedTeamMemberIds=selected_team_member_ids,
        requireClarification=clarification is not None,
        clarification=clarification,
        toolScope=tool_scope,
        mainAgentInstruction=_main_agent_instruction(runtime.kind),
    )


def _sanitize_runtime_route(
    *,
    requested: RuntimeRoute,
    fallback: RuntimeRoute,
    payload_confidence: float,
    payload_reason: str,
    has_team: bool,
    clarification: dict[str, Any] | None,
) -> RuntimeRoute:
    """把模型返回的 runtime route 裁剪成当前支持的第一层线路。"""

    if clarification:
        return RuntimeRoute(
            kind="clarification",
            confidence=max(0.0, min(float(payload_confidence), 1.0)),
            reason=payload_reason or "模型判断执行前需要补充信息。",
        )
    if has_team:
        return RuntimeRoute(
            kind="team",
            confidence=max(0.0, min(float(requested.confidence or payload_confidence), 1.0)),
            reason=requested.reason or payload_reason or "用户选择了 Team，进入 Team Runtime。",
            config=requested.config,
        )
    if requested.kind not in {"react", "team", "clarification"}:
        return fallback
    if requested.kind == "team" and not has_team:
        return RuntimeRoute(
            kind="react",
            confidence=max(0.0, min(float(requested.confidence or payload_confidence), 1.0)),
            reason="未选择 Team，模型请求的 Team Runtime 已降级为 ReAct Runtime。",
            config=requested.config,
        )
    return RuntimeRoute(
        kind=requested.kind if requested.kind != "clarification" else "react",
        confidence=max(0.0, min(float(requested.confidence or payload_confidence), 1.0)),
        reason=requested.reason or payload_reason or "进入 ReAct Runtime。",
        config=requested.config,
    )


def _normalize_clarification(value: dict[str, Any] | None) -> dict[str, Any] | None:
    """清理模型返回的追问结构。"""

    if not isinstance(value, dict):
        return None
    questions = value.get("questions")
    if not isinstance(questions, list):
        questions = []
    normalized_questions = []
    for index, item in enumerate(questions[:5]):
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or item.get("question") or "").strip()
        if not label:
            continue
        normalized_questions.append({
            "id": str(item.get("id") or f"question_{index + 1}")[:40],
            "type": str(item.get("type") or "text"),
            "label": label[:120],
            **({"options": item.get("options")} if isinstance(item.get("options"), list) else {}),
        })
    if not normalized_questions:
        return None
    return {
        "title": str(value.get("title") or "需要补充信息")[:80],
        "description": str(value.get("description") or "")[:240],
        "questions": normalized_questions,
    }


def _extract_json_object(text: str) -> dict[str, Any]:
    """从模型输出中提取第一个 JSON 对象。"""

    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(raw[start : end + 1])
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}


def _skill_summaries(skill_ids: list[str]) -> list[dict[str, str]]:
    """生成模型路由可见的 Skill 摘要。"""

    summaries: list[dict[str, str]] = []
    for skill_id in skill_ids:
        skill = get_installed_skill(skill_id)
        if not skill:
            continue
        summaries.append({
            "id": skill.id,
            "name": skill.name,
            "description": skill.description,
            "trigger": skill.trigger,
            "category": skill.category,
            "tags": ", ".join(skill.tags),
        })
    return summaries


async def _fallback_route_with_optional_team_refinement(
    route: IntentRoute,
    *,
    message: str,
    agent_id: str,
    team_name: str | None,
    team_members: list[ChatTeamMemberPayload],
    model_name: str,
) -> IntentRoute:
    """模型路由失败时保留旧的 Team worker 模型兜底能力。"""

    return await refine_team_route_with_model(
        route,
        message=message,
        agent_id=agent_id,
        team_name=team_name,
        team_members=team_members,
        model_name=model_name,
    )


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
    runtime = RuntimeRoute(
        kind="team",
        confidence=0.82,
        reason=reason or f"模型路由选择 Worker: {', '.join(selected)}",
    )
    return route.model_copy(update={
        "mode": runtime.kind,
        "runtime": runtime,
        "decisions": decisions,
        "selected_team_member_ids": selected,
        "main_agent_instruction": _main_agent_instruction(runtime.kind),
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


def _detect_tool_scope(
    *,
    message: str,
    agent_config: object | None,
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
    allow_mcp = configured_mcp and (needs_mcp or needs_network or has_team)
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


def _route_runtime(
    *,
    has_team: bool,
    clarification: dict[str, Any] | None,
) -> RuntimeRoute:
    """规则兜底的第一层运行线路。"""

    if clarification:
        return RuntimeRoute(
            kind="clarification",
            confidence=0.82,
            reason="规则判断执行前需要补充信息。",
        )
    if has_team:
        return RuntimeRoute(
            kind="team",
            confidence=0.8,
            reason="用户选择了 Team，进入 Team Runtime。",
        )
    return RuntimeRoute(
        kind="react",
        confidence=0.78,
        reason="进入默认 ReAct Runtime。",
    )


def _main_agent_instruction(mode: RuntimeKind) -> str:
    if mode == "react":
        return "本轮按 ReAct Runtime 处理；不要主动输出计划或调用子 Agent。"
    if mode == "team":
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
