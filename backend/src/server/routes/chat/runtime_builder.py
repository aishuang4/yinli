"""Chat Runtime 组装辅助。

这里承接前置 IntentRoute 之后的纯编排逻辑，避免 send.py 同时负责
路由、运行时配置、缓存 key 和 SSE 事件处理。
"""

from dataclasses import dataclass
from typing import Any, Callable

from src.agents.factory import (
    AgentModelOptions,
    AgentGuardrails,
    KnowledgeToolRuntimeSpec,
    McpRuntimeSpec,
    RuntimeToolScope,
    SubagentRuntimeContext,
    SubagentRuntimeSpec,
    get_or_create_agent,
)
from src.server.routes.chat.intent_router import (
    IntentRoute,
    filter_team_members_for_route,
)
from src.server.routes.chat.models import ChatMessage, ChatTeamMemberPayload
from src.server.routes.chat.plans import build_plan_context_prompt, create_model_first_runtime_plan
from src.server.routes.chat.prompts import _build_agent_plan_prompt, _build_team_system_prompt
from src.server.routes.skills import Skill, get_installed_skill


@dataclass(frozen=True)
class RuntimeModelConfig:
    """本轮模型运行配置。"""

    model_name: str
    fallback_names: list[str]
    model_options: AgentModelOptions


@dataclass(frozen=True)
class RoutedTeamContext:
    """按 IntentRoute 裁剪后的 Team 上下文。"""

    members: list[ChatTeamMemberPayload]
    run_data: ChatMessage
    system_prompt: str


@dataclass(frozen=True)
class PlanRuntimeBuildResult:
    """Plan Runtime 构建结果。"""

    system_prompt: str
    runtime_plan: dict[str, Any] | None


@dataclass(frozen=True)
class MainAgentRuntimeSpec:
    """创建主 Agent 所需的稳定运行时规格。"""

    runtime_agent_id: str
    name: str
    system_prompt: str
    model_name: str
    fallback_names: list[str]
    thinking_enable: bool
    model_options: AgentModelOptions
    guardrails: AgentGuardrails
    mcp_servers: list[McpRuntimeSpec]
    knowledge_tools: list[KnowledgeToolRuntimeSpec]
    subagents: list[SubagentRuntimeSpec]
    subagent_context: SubagentRuntimeContext
    parent_agent_id: str
    parent_agent_name: str
    plan_id: str | None
    tool_scope: RuntimeToolScope


def build_runtime_model_config(
    *,
    run_data: ChatMessage,
    agent_config: object | None,
    runtime_model_settings: Any,
) -> RuntimeModelConfig:
    """合成本轮实际使用的主模型、fallback 和采样参数。"""

    model_config_data = agent_config.model_config_data if agent_config else None
    model_name = (
        run_data.model
        or (model_config_data.primary if model_config_data else None)
        or runtime_model_settings.default_model
    )
    fallback_names = model_config_data.fallback if model_config_data else runtime_model_settings.fallback_models
    return RuntimeModelConfig(
        model_name=model_name,
        fallback_names=fallback_names,
        model_options=AgentModelOptions(
            temperature=model_config_data.temperature if model_config_data else None,
            top_p=model_config_data.top_p if model_config_data else None,
            max_tokens=model_config_data.max_tokens if model_config_data else None,
            max_react_iters=int(getattr(model_config_data, "max_react_iters", 40) or 40) if model_config_data else 40,
        ),
    )


def build_routed_team_context(
    *,
    run_data: ChatMessage,
    intent_route: IntentRoute,
    leader_agent_id: str,
) -> RoutedTeamContext:
    """根据路由结果生成本轮 Team 成员和 Leader 系统提示词。"""

    members = filter_team_members_for_route(
        run_data.team_members,
        intent_route.selected_team_member_ids,
        leader_agent_id,
    )
    return RoutedTeamContext(
        members=members,
        run_data=run_data.model_copy(update={"team_members": members}),
        system_prompt=_build_team_system_prompt(run_data.team_name, members),
    )


def select_route_skills(intent_route: IntentRoute, active_skill: Skill | None) -> list[Skill]:
    """按路由结果选择本轮 Skill；正在恢复的 Skill 优先。"""

    if active_skill:
        return [active_skill]
    return [
        skill
        for skill_id in intent_route.selected_skill_ids
        if (skill := get_installed_skill(skill_id)) is not None
    ]


def runtime_tool_scope_from_route(intent_route: IntentRoute) -> RuntimeToolScope:
    """把 API 层 ToolScope 转换为 Agent 工厂使用的 RuntimeToolScope。"""

    return RuntimeToolScope(
        allow_mcp=intent_route.tool_scope.allow_mcp,
        allow_knowledge=intent_route.tool_scope.allow_knowledge,
        allow_bash=intent_route.tool_scope.allow_bash,
        allow_http=intent_route.tool_scope.allow_http,
    )


async def build_plan_runtime_context(
    *,
    intent_route: IntentRoute,
    agent_config: object | None,
    session_id: str,
    agent_id: str,
    team_id: str | None,
    title: str,
    goal: str,
    team_members: list[ChatTeamMemberPayload],
    has_tools: bool,
    has_skills: bool,
    system_prompt: str,
    model_config: RuntimeModelConfig,
) -> PlanRuntimeBuildResult:
    """按 IntentRoute 创建 Plan Runtime，并把 Plan 上下文追加到系统提示词。"""

    if not intent_route.should_create_plan:
        return PlanRuntimeBuildResult(system_prompt=system_prompt, runtime_plan=None)

    plan_config = getattr(agent_config, "plan_config", None)
    runtime_plan = await create_model_first_runtime_plan(
        session_id=session_id,
        agent_id=agent_id,
        team_id=team_id,
        title=title,
        goal=goal,
        mode=str(getattr(plan_config, "mode", "structured") or "structured"),
        max_steps=int(getattr(plan_config, "max_steps", 6) or 6),
        team_members=team_members,
        has_tools=has_tools,
        has_skills=has_skills,
        model_name=model_config.model_name,
        fallback_names=model_config.fallback_names,
        model_options=model_config.model_options,
    )
    next_system_prompt = "\n\n".join(
        part
        for part in [
            system_prompt,
            _build_agent_plan_prompt(agent_config),
            build_plan_context_prompt(runtime_plan),
        ]
        if part
    )
    return PlanRuntimeBuildResult(system_prompt=next_system_prompt, runtime_plan=runtime_plan)


def build_runtime_agent_cache_key(
    *,
    agent_id: str,
    session_id: str,
    model_name: str,
    deep_thinking: bool,
    fallback_names: list[str],
    used_skills: list[Skill],
    mcp_servers: list[McpRuntimeSpec],
    knowledge_tools: list[KnowledgeToolRuntimeSpec],
    subagents: list[SubagentRuntimeSpec],
    subagent_context: SubagentRuntimeContext,
    runtime_tool_scope: RuntimeToolScope,
    runtime_plan: dict[str, Any] | None,
    guardrail_cache_key: str,
) -> str:
    """生成影响 Agent 实例缓存的稳定 key。"""

    skill_cache_key = ",".join(sorted(skill.id for skill in used_skills))
    fallback_cache_key = ",".join(fallback_names)
    mcp_cache_key = ",".join(
        f"{server.name}:{','.join(server.enable_tools)}"
        for server in mcp_servers
    )
    knowledge_cache_key = ",".join(tool.id for tool in knowledge_tools)
    subagent_cache_key = ",".join(f"{item.id}:{item.name}" for item in subagents)
    return (
        f"{agent_id}:{session_id}:{model_name}:thinking-{deep_thinking}:"
        f"fallbacks-{fallback_cache_key}:"
        f"skills-{skill_cache_key}:mcp-{mcp_cache_key}:knowledge-{knowledge_cache_key}:"
        f"subagents-{subagent_cache_key}:depth-{subagent_context.max_depth}:"
        f"tools-{runtime_tool_scope.cache_key()}:"
        f"plan-{runtime_plan.get('id') if runtime_plan else 'none'}:"
        f"guardrails-{guardrail_cache_key}"
    )


def build_main_agent_runtime_spec(
    *,
    agent_id: str,
    agent_name: str,
    session_id: str,
    system_prompt: str,
    model_config: RuntimeModelConfig,
    deep_thinking: bool,
    used_skills: list[Skill],
    guardrails: AgentGuardrails,
    mcp_servers: list[McpRuntimeSpec],
    knowledge_tools: list[KnowledgeToolRuntimeSpec],
    subagents: list[SubagentRuntimeSpec],
    subagent_context: SubagentRuntimeContext,
    runtime_tool_scope: RuntimeToolScope,
    runtime_plan: dict[str, Any] | None,
    guardrail_cache_key: str,
) -> MainAgentRuntimeSpec:
    """构建主 Agent 工厂参数，不包含 trace 回调。"""

    runtime_agent_id = build_runtime_agent_cache_key(
        agent_id=agent_id,
        session_id=session_id,
        model_name=model_config.model_name,
        deep_thinking=deep_thinking,
        fallback_names=model_config.fallback_names,
        used_skills=used_skills,
        mcp_servers=mcp_servers,
        knowledge_tools=knowledge_tools,
        subagents=subagents,
        subagent_context=subagent_context,
        runtime_tool_scope=runtime_tool_scope,
        runtime_plan=runtime_plan,
        guardrail_cache_key=guardrail_cache_key,
    )
    return MainAgentRuntimeSpec(
        runtime_agent_id=runtime_agent_id,
        name=agent_name,
        system_prompt=system_prompt,
        model_name=model_config.model_name,
        fallback_names=model_config.fallback_names,
        thinking_enable=deep_thinking,
        model_options=model_config.model_options,
        guardrails=guardrails,
        mcp_servers=mcp_servers,
        knowledge_tools=knowledge_tools,
        subagents=subagents,
        subagent_context=subagent_context,
        parent_agent_id=agent_id,
        parent_agent_name=agent_name,
        plan_id=str(runtime_plan.get("id")) if runtime_plan else None,
        tool_scope=runtime_tool_scope,
    )


def create_main_agent_from_spec(
    spec: MainAgentRuntimeSpec,
    *,
    on_fallback_event: Callable[[dict[str, Any]], None] | None = None,
    on_subagent_event: Callable[[dict[str, Any]], None] | None = None,
) -> Any:
    """根据 MainAgentRuntimeSpec 创建或复用主 Agent。"""

    return get_or_create_agent(
        agent_id=spec.runtime_agent_id,
        name=spec.name,
        system_prompt=spec.system_prompt,
        model_name=spec.model_name,
        fallback_names=spec.fallback_names,
        thinking_enable=spec.thinking_enable,
        permission_mode=spec.guardrails.permission_profile.to_agentscope_permission_mode(),
        model_options=spec.model_options,
        guardrails=spec.guardrails,
        mcp_servers=spec.mcp_servers,
        knowledge_tools=spec.knowledge_tools,
        subagents=spec.subagents,
        subagent_context=spec.subagent_context,
        parent_agent_id=spec.parent_agent_id,
        parent_agent_name=spec.parent_agent_name,
        plan_id=spec.plan_id,
        tool_scope=spec.tool_scope,
        on_fallback_event=on_fallback_event,
        on_subagent_event=on_subagent_event,
    )
