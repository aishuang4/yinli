"""主 Agent 运行时规格。

Chat、Team Member 和群聊 Worker 共用这一份规格、缓存 key 和工厂创建。
Chat 自己的模型选择、Skill 选择和工具范围仍留在 routes/chat/runtime_builder.py。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import os
from typing import Any, Callable

from agentscope.middleware import MiddlewareBase
from agentscope.tool import ToolBase

from src.agents.factory import (
    AgentGuardrails,
    AgentModelOptions,
    KnowledgeToolRuntimeSpec,
    McpRuntimeSpec,
    RuntimeToolScope,
    get_or_create_agent,
)
from src.agents.runtime_types import SKILL_LOADING_INTERACTIVE, normalize_skill_loading_mode
from src.agents.session_context import session_context_key
from src.runtime.authorization import RunAuthorizationBinding
from src.skills.registry import Skill


@dataclass(frozen=True)
class RuntimeModelConfig:
    """本轮模型运行配置。"""

    model_name: str
    fallback_names: list[str]
    model_options: AgentModelOptions


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
    skill_paths: list[str]
    workspace_dir: str = ""
    write_scope_dir: str = ""
    include_default_tools: bool = True
    extra_filesystem_scopes: list[str] = field(default_factory=list)
    authorization: RunAuthorizationBinding | None = None
    context_scope: str = ""
    # interactive_on_demand：模型按需调用 Skill viewer；scheduled_preloaded：只用预加载的 required Skill。
    skill_loading_mode: str = SKILL_LOADING_INTERACTIVE
    run_snapshot: dict[str, Any] | None = None

    @property
    def session_context_key(self) -> str:
        """同一持久 Agent + session 的对话上下文标识，供工厂做实例间上下文搬运。"""

        if not self.session_id:
            return ""
        base = session_context_key(self.parent_agent_id, self.session_id)
        if self.context_scope:
            return f"{base}:{self.context_scope}"
        return base


def build_runtime_agent_cache_key(
    *,
    agent_id: str,
    session_id: str,
    system_prompt: str,
    model_name: str,
    deep_thinking: bool,
    fallback_names: list[str],
    used_skills: list[Skill],
    skill_paths: list[str] | None = None,
    mcp_servers: list[McpRuntimeSpec],
    knowledge_tools: list[KnowledgeToolRuntimeSpec],
    runtime_tool_scope: RuntimeToolScope,
    guardrail_cache_key: str,
    runtime_features_cache_key: str = "none",
    run_snapshot_id: str = "",
    context_scope: str = "",
    skill_loading_mode: str = SKILL_LOADING_INTERACTIVE,
) -> str:
    """生成影响 Agent 实例缓存的稳定 key。

    Skill 加载模式决定模型可见工具集（是否有 `Skill` viewer），必须进 key：
    同一 Agent / Session 的交互式与 scheduled 触发不能复用同一实例。
    """

    skill_loading_mode = normalize_skill_loading_mode(skill_loading_mode)
    skill_cache_parts: list[str] = []
    for path in sorted(skill_paths or []):
        skill_md = os.path.join(path, "SKILL.md")
        try:
            modified_ns = os.stat(skill_md).st_mtime_ns
        except OSError:
            modified_ns = 0
        skill_cache_parts.append(f"{path}:{modified_ns}")
    skill_cache_key = hashlib.sha256("\n".join(skill_cache_parts).encode("utf-8")).hexdigest()[:16]
    fallback_cache_key = ",".join(fallback_names)
    mcp_cache_key = ",".join(
        f"{server.name}:{','.join(server.enable_tools)}"
        for server in mcp_servers
    )
    knowledge_cache_key = ",".join(tool.id for tool in knowledge_tools)
    from src.catalog.loader import catalog_fingerprint

    # system prompt 正文刻意不进 key：它每轮都会随记忆摘要、Skill Run 等变化，
    # 进 key 会导致每轮新建实例、AgentScope 会话记忆丢失并且旧实例泄漏。
    # 工厂在复用实例时会直接刷新 `_system_prompt`。
    del system_prompt
    skill_cache_key = hashlib.sha256((skill_cache_key + str([(item.id, item.load_name, item.content_hash, item.catalog_version) for item in used_skills])).encode()).hexdigest()[:16]
    return (
        f"{agent_id}:{session_id}:{model_name}:thinking-{deep_thinking}:"
        f"fallbacks-{fallback_cache_key}:"
        f"skills-{skill_cache_key}:mcp-{mcp_cache_key}:knowledge-{knowledge_cache_key}:"
        f"tools-{runtime_tool_scope.cache_key()}:"
        f"skill-loading-{skill_loading_mode}:"
        f"features-{runtime_features_cache_key}:"
        f"guardrails-{guardrail_cache_key}:"
        f"catalog-{catalog_fingerprint()}"
        + (f":snapshot-{run_snapshot_id}" if run_snapshot_id else "")
        + (f":scope-{context_scope}" if context_scope else "")
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
    skill_paths: list[str] | None = None,
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
    workspace_dir: str = "",
    write_scope_dir: str = "",
    include_default_tools: bool = True,
    extra_filesystem_scopes: list[str] | None = None,
    authorization: RunAuthorizationBinding | None = None,
    run_snapshot: dict[str, Any],
    apply_snapshot_filesystem: bool = True,
    context_scope: str = "",
    skill_loading_mode: str = SKILL_LOADING_INTERACTIVE,
) -> MainAgentRuntimeSpec:
    """构建主 Agent 工厂参数，不包含 trace 回调。"""

    skill_loading_mode = normalize_skill_loading_mode(skill_loading_mode)
    if run_snapshot is None:
        from src.sessions.errors import SessionError

        raise SessionError("snapshot_required", "Run 没有冻结快照")
    snapshot_id = ""
    if run_snapshot is not None:
        from src.sessions.factory_snapshot import apply_run_snapshot_to_factory

        applied = apply_run_snapshot_to_factory(
            run_snapshot,
            guardrails=guardrails,
            skill_paths=skill_paths,
            mcp_servers=mcp_servers,
            knowledge_tools=knowledge_tools,
            used_skills=used_skills,
            apply_filesystem=apply_snapshot_filesystem,
        )
        snapshot_id = applied.snapshot_id
        skill_paths = list(applied.skill_paths)
        mcp_servers = list(applied.mcp_servers)
        knowledge_tools = list(applied.knowledge_tools)
        used_skills = list(applied.used_skills)
        if apply_snapshot_filesystem:
            guardrails = applied.guardrails
            workspace_dir = applied.workspace_dir
            write_scope_dir = applied.write_scope_dir
            extra_filesystem_scopes = list(applied.extra_filesystem_scopes)
    runtime_agent_id = build_runtime_agent_cache_key(
        agent_id=agent_id,
        session_id=session_id,
        system_prompt=system_prompt,
        model_name=model_config.model_name,
        deep_thinking=deep_thinking,
        fallback_names=model_config.fallback_names,
        used_skills=used_skills,
        skill_paths=skill_paths,
        mcp_servers=mcp_servers,
        knowledge_tools=knowledge_tools,
        runtime_tool_scope=runtime_tool_scope,
        guardrail_cache_key=guardrail_cache_key,
        runtime_features_cache_key=runtime_features_cache_key,
        run_snapshot_id=snapshot_id,
        context_scope=context_scope,
        skill_loading_mode=skill_loading_mode,
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
        skill_paths=list(skill_paths or []),
        workspace_dir=workspace_dir,
        write_scope_dir=write_scope_dir,
        include_default_tools=include_default_tools,
        extra_filesystem_scopes=list(extra_filesystem_scopes or []),
        authorization=authorization,
        context_scope=context_scope,
        skill_loading_mode=skill_loading_mode,
        run_snapshot=run_snapshot,
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
        skill_paths=spec.skill_paths,
        workspace_dir=spec.workspace_dir or None,
        write_scope_dir=spec.write_scope_dir or None,
        include_default_tools=spec.include_default_tools,
        extra_filesystem_scopes=spec.extra_filesystem_scopes,
        session_context_key=spec.session_context_key or None,
        authorization=spec.authorization,
        skill_loading_mode=spec.skill_loading_mode,
        run_snapshot=spec.run_snapshot,
    )
