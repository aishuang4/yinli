"""从配置创建或复用 AgentScope Agent。

模型链在 `model_factory.py`，工具装配在 `toolkit_builder.py`，
上下文压缩在 `context_config.py`，目录范围在 `workspace_paths.py`，
实例缓存在 `agent_cache.py`。本文件只决定这一轮用哪一份配置把它们拼起来。
"""

import logging
import time
from pathlib import Path

from agentscope.agent import Agent
from agentscope.agent._config import ModelConfig, ReActConfig
from agentscope.middleware import MiddlewareBase
from agentscope.permission import PermissionContext
from agentscope.permission._types import PermissionMode
from agentscope.state import AgentState
from agentscope.tool import ToolBase
from agentscope.workspace import DockerWorkspace, LocalWorkspace

from src.agents.agent_cache import (
    AGENT_CACHE_IDLE_SECONDS,
    AGENT_CACHE_MAX_SIZE,
    _BUSY_ATTR,
    _agent_cache,
    _agent_last_used,
    _cache_key,
    _evict_idle_agents,
    _touch_agent,
    clear_all_agents,
    has_cached_agent,
    invalidate_agent,
    mark_agent_busy,
    mark_agent_idle,
    remember_agent,
)
from src.agents.agentscope_usage_middleware import ProviderCacheUsageMiddleware
from src.agents.context_config import _build_context_config, _build_injection_config
from src.agents.model_factory import create_model_chain
from src.agents.model_options import MAX_REACT_ITERS, AgentModelOptions, FallbackEventCallback
from src.agents.guardrails import AgentGuardrails
from src.agents.tool_specs import KnowledgeToolRuntimeSpec, McpRuntimeSpec, RuntimeToolScope
from src.agents.session_context import settle_awaiting_tool_calls
from src.agents.toolkit_builder import _create_default_toolkit
from src.agents.workspace_paths import (
    _resolve_write_tool_base,
    _safe_workspace_dir,
    _seed_catalog_profiles_readonly,
)
from src.config.runtime_paths import sandbox_root
from src.config.runtime_settings import binding_for_model

logger = logging.getLogger(__name__)

__all__ = [
    "AGENT_CACHE_IDLE_SECONDS",
    "AGENT_CACHE_MAX_SIZE",
    "AgentGuardrails",
    "AgentModelOptions",
    "KnowledgeToolRuntimeSpec",
    "McpRuntimeSpec",
    "RuntimeToolScope",
    "_build_context_config",
    "_cache_key",
    "_create_default_toolkit",
    "_evict_idle_agents",
    "_resolve_write_tool_base",
    "_safe_workspace_dir",
    "_seed_catalog_profiles_readonly",
    "clear_all_agents",
    "get_or_create_agent",
    "has_cached_agent",
    "invalidate_agent",
    "mark_agent_busy",
    "mark_agent_idle",
]


def get_or_create_agent(
    agent_id: str = "default",
    name: str = "Assistant",
    system_prompt: str = "You are a helpful assistant.",
    model_name: str | None = None,
    fallback_names: list[str] | None = None,
    thinking_enable: bool = False,
    permission_mode: PermissionMode = PermissionMode.BYPASS,
    model_options: AgentModelOptions | None = None,
    guardrails: AgentGuardrails | None = None,
    mcp_servers: list[McpRuntimeSpec] | None = None,
    knowledge_tools: list[KnowledgeToolRuntimeSpec] | None = None,
    parent_agent_id: str | None = None,
    parent_agent_name: str | None = None,
    session_id: str = "",
    tool_scope: RuntimeToolScope | None = None,
    extra_tools: list[ToolBase] | None = None,
    include_default_tools: bool = True,
    tool_name_allowlist: set[str] | None = None,
    tool_name_denylist: set[str] | None = None,
    on_fallback_event: FallbackEventCallback | None = None,
    middlewares: list[MiddlewareBase] | None = None,
    skill_paths: list[str] | None = None,
    skill_loading_mode: str = "interactive_on_demand",
    workspace_dir: str | None = None,
    write_scope_dir: str | None = None,
    extra_filesystem_scopes: list[str] | None = None,
    session_context_key: str | None = None,
    project_slug: str = "default",
    platform_binding: object | None = None,
    authorization: object | None = None,
) -> Agent:
    """优先从缓存取实例。配置变化或实例正忙时新建，并搬运会话上下文。"""

    del fallback_names
    binding = binding_for_model(model_name)
    from src.tenancy.capabilities import coerce_hosted_guardrails
    from src.tenancy.context import current_tenant

    if platform_binding is None:
        platform_binding = authorization
    guardrails = coerce_hosted_guardrails(guardrails)
    if current_tenant().is_hosted and "Bash 中的 /workspace 就是项目根目录" not in system_prompt:
        system_prompt = f"{system_prompt}\nBash 中的 /workspace 就是项目根目录。"
    if platform_binding is not None and hasattr(platform_binding, "cache_key"):
        agent_id = f"{agent_id}:auth-{platform_binding.cache_key()}"
    agent_id = _cache_key(agent_id)
    if session_context_key:
        session_context_key = _cache_key(session_context_key)
    cached = _agent_cache.get(agent_id)
    if cached is not None and getattr(cached, "_model_binding", None) == binding:
        if getattr(cached, _BUSY_ATTR, None):
            logger.warning("Agent %s 仍在运行，改用快照实例承接新一轮", agent_id)
            agent_id = f"{agent_id}:fork-{int(time.time() * 1000)}"
        elif platform_binding is not None and not hasattr(cached, "bind_authorization"):
            agent_id = f"{agent_id}:platform"
        else:
            _touch_agent(agent_id)
            if getattr(cached, "_system_prompt", None) != system_prompt:
                cached._system_prompt = system_prompt
            if on_fallback_event is not None and hasattr(cached.model, "on_event"):
                cached.model.on_event = on_fallback_event
            settle_awaiting_tool_calls(cached)
            if platform_binding is not None:
                cached.bind_authorization(platform_binding)
            return cached

    primary_model = binding.primary.default_model
    fallbacks = [item.default_model for item in binding.chain[1:]]
    main_model = create_model_chain(
        [primary_model, *fallbacks],
        thinking_enable=thinking_enable,
        model_options=model_options,
        on_fallback_event=on_fallback_event,
        resolved_models=binding.chain,
    )

    docker_skill_runtime_root: str | None = None
    if current_tenant().is_hosted:
        from src.sandbox.hosted_workspace import HostedDockerWorkspace, open_hosted_workspace

        workspace = open_hosted_workspace(
            tenant_id=current_tenant().tenant_id,
            project_slug=project_slug or "default",
            agent_id=agent_id,
            base_image=(guardrails.docker_image if guardrails else "") or "python:3.11-slim",
            network_enabled=bool(guardrails.network_enabled) if guardrails else True,
        )
        if not isinstance(workspace, HostedDockerWorkspace):
            raise RuntimeError("托管命令只能在沙箱中执行")
        docker_skill_runtime_root = "/workspace/skills" if skill_paths else None
    elif guardrails and guardrails.execution_backend == "docker":
        if skill_paths and not guardrails.persistent_sandbox:
            raise ValueError("DockerWorkspace 使用 Skills 时必须启用 persistent_sandbox。")
        docker_skill_runtime_root = "/workspace/skills" if skill_paths else None
        host_dir = sandbox_root() / agent_id.replace(":", "-").replace("/", "-")
        workspace = DockerWorkspace(
            base_image=guardrails.docker_image or "python:3.11-slim",
            host_workdir=str(host_dir) if guardrails.persistent_sandbox else None,
            skill_paths=skill_paths,
        )
        if guardrails.persistent_sandbox and extra_filesystem_scopes:
            _seed_catalog_profiles_readonly(Path(host_dir), extra_filesystem_scopes)
    else:
        workspace = LocalWorkspace(workdir=workspace_dir or _safe_workspace_dir(guardrails))

    agent_state = AgentState(
        permission_context=PermissionContext(mode=permission_mode),
    )
    agent_cls = Agent
    agent_kwargs: dict = {}
    if platform_binding is not None:
        from src.agents.permission_adapter import PlatformAgent

        agent_cls = PlatformAgent
        agent_kwargs = {
            "authorization_service": platform_binding.service,
            "action_builder": platform_binding.action_builder,
        }
    agent = agent_cls(
        name=name,
        system_prompt=system_prompt,
        model=main_model,
        toolkit=_create_default_toolkit(
            guardrails=guardrails,
            mcp_servers=mcp_servers,
            knowledge_tools=knowledge_tools,
            parent_agent_id=parent_agent_id or agent_id,
            parent_agent_name=parent_agent_name or name,
            session_id=session_id,
            tool_scope=tool_scope,
            extra_tools=extra_tools,
            include_default_tools=include_default_tools,
            tool_name_allowlist=tool_name_allowlist,
            tool_name_denylist=tool_name_denylist,
            skill_paths=skill_paths,
            skill_runtime_root=docker_skill_runtime_root,
            register_skill_catalog=skill_loading_mode != "scheduled_preloaded",
            workspace_dir=workspace_dir,
            write_scope_dir=write_scope_dir,
            extra_filesystem_scopes=extra_filesystem_scopes,
        ),
        state=agent_state,
        offloader=workspace,
        middlewares=[*(middlewares or []), ProviderCacheUsageMiddleware()],
        model_config=ModelConfig(max_retries=3, fallback_model=None),
        context_config=_build_context_config(),
        react_config=ReActConfig(max_iters=MAX_REACT_ITERS),
        injection_config=_build_injection_config(),
        **agent_kwargs,
    )
    if platform_binding is not None:
        agent.bind_authorization(platform_binding)

    agent._model_binding = binding
    remember_agent(agent_id, agent, session_context_key)
    return agent
