"""Chat Runtime 组装辅助。

这里承接前置 IntentRoute 之后的纯编排逻辑，避免 send.py 同时负责
路由、运行时配置、缓存 key 和 SSE 事件处理。
"""

from dataclasses import dataclass
import hashlib
from typing import Any, Callable

from agentscope.middleware import MiddlewareBase
from agentscope.tool import ToolBase

from src.agents.factory import (
    AgentModelOptions,
    AgentGuardrails,
    KnowledgeToolRuntimeSpec,
    McpRuntimeSpec,
    RuntimeToolScope,
    get_or_create_agent,
)
from src.server.routes.chat.models import ChatMessage, ChatTeamMemberPayload
from src.server.routes.chat.prompts import _build_team_system_prompt
from src.server.routes.chat.skill_selector import select_runtime_skills
from src.server.routes.chat.tool_scope_resolver import resolve_runtime_tool_scope
from src.server.routes.skills import Skill


@dataclass(frozen=True)
class RuntimeModelConfig:
    """本轮模型运行配置。"""

    model_name: str
    fallback_names: list[str]
    model_options: AgentModelOptions


@dataclass(frozen=True)
class RoutedTeamContext:
    """完整 Team 上下文。Worker 选择由 Team Runtime 内部处理。"""

    members: list[ChatTeamMemberPayload]
    run_data: ChatMessage
    system_prompt: str


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
    parent_agent_id: str
    parent_agent_name: str
    session_id: str
    tool_scope: RuntimeToolScope
    extra_tools: list[ToolBase]
    tool_name_allowlist: set[str] | None
    tool_name_denylist: set[str]
    middlewares: list[MiddlewareBase]


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
    leader_agent_id: str,
) -> RoutedTeamContext:
    """生成完整 Team 上下文和 Leader 系统提示词。"""

    members = run_data.team_members
    return RoutedTeamContext(
        members=members,
        run_data=run_data.model_copy(update={"team_members": members}),
        system_prompt=_build_team_system_prompt(run_data.team_name, members),
    )


def build_runtime_skills(
    *,
    skill_ids: list[str],
    message: str,
    active_skill: Skill | None,
) -> list[Skill]:
    """选择本轮 Skill；正在恢复的 Skill 优先。"""

    return select_runtime_skills(
        skill_ids=skill_ids,
        message=message,
        active_skill=active_skill,
    )


def resolve_react_runtime_tool_scope(
    *,
    message: str,
    agent_config: object | None,
    has_selected_skills: bool,
    has_team: bool,
    has_images: bool,
) -> RuntimeToolScope:
    """解析 ReAct Runtime 使用的工具范围。"""

    return resolve_runtime_tool_scope(
        message=message,
        agent_config=agent_config,
        has_selected_skills=has_selected_skills,
        has_team=has_team,
        has_images=has_images,
    ).scope


def build_runtime_agent_cache_key(
    *,
    agent_id: str,
    session_id: str,
    system_prompt: str,
    model_name: str,
    deep_thinking: bool,
    fallback_names: list[str],
    used_skills: list[Skill],
    mcp_servers: list[McpRuntimeSpec],
    knowledge_tools: list[KnowledgeToolRuntimeSpec],
    runtime_tool_scope: RuntimeToolScope,
    guardrail_cache_key: str,
    runtime_features_cache_key: str = "none",
) -> str:
    """生成影响 Agent 实例缓存的稳定 key。"""

    skill_cache_key = ",".join(sorted(skill.id for skill in used_skills))
    fallback_cache_key = ",".join(fallback_names)
    mcp_cache_key = ",".join(
        f"{server.name}:{','.join(server.enable_tools)}"
        for server in mcp_servers
    )
    knowledge_cache_key = ",".join(tool.id for tool in knowledge_tools)
    system_prompt_cache_key = hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()[:16]
    return (
        f"{agent_id}:{session_id}:{model_name}:thinking-{deep_thinking}:"
        f"fallbacks-{fallback_cache_key}:"
        f"skills-{skill_cache_key}:mcp-{mcp_cache_key}:knowledge-{knowledge_cache_key}:"
        f"tools-{runtime_tool_scope.cache_key()}:"
        f"prompt-{system_prompt_cache_key}:"
        f"features-{runtime_features_cache_key}:"
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
    runtime_tool_scope: RuntimeToolScope,
    guardrail_cache_key: str,
    runtime_features_cache_key: str = "none",
    extra_tools: list[ToolBase] | None = None,
    tool_name_allowlist: set[str] | None = None,
    tool_name_denylist: set[str] | None = None,
    middlewares: list[MiddlewareBase] | None = None,
) -> MainAgentRuntimeSpec:
    """构建主 Agent 工厂参数，不包含 trace 回调。"""

    runtime_agent_id = build_runtime_agent_cache_key(
        agent_id=agent_id,
        session_id=session_id,
        system_prompt=system_prompt,
        model_name=model_config.model_name,
        deep_thinking=deep_thinking,
        fallback_names=model_config.fallback_names,
        used_skills=used_skills,
        mcp_servers=mcp_servers,
        knowledge_tools=knowledge_tools,
        runtime_tool_scope=runtime_tool_scope,
        guardrail_cache_key=guardrail_cache_key,
        runtime_features_cache_key=runtime_features_cache_key,
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
        parent_agent_id=agent_id,
        parent_agent_name=agent_name,
        session_id=session_id,
        tool_scope=runtime_tool_scope,
        extra_tools=extra_tools or [],
        tool_name_allowlist=tool_name_allowlist,
        tool_name_denylist=tool_name_denylist or set(),
        middlewares=middlewares or [],
    )


def create_main_agent_from_spec(
    spec: MainAgentRuntimeSpec,
    *,
    on_fallback_event: Callable[[dict[str, Any]], None] | None = None,
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
        parent_agent_id=spec.parent_agent_id,
        parent_agent_name=spec.parent_agent_name,
        session_id=spec.session_id,
        tool_scope=spec.tool_scope,
        on_fallback_event=on_fallback_event,
        extra_tools=spec.extra_tools,
        tool_name_allowlist=spec.tool_name_allowlist,
        tool_name_denylist=spec.tool_name_denylist,
        middlewares=spec.middlewares,
    )
