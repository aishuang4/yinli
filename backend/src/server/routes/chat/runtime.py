"""运行时配置构建 — guardrails、权限模式、MCP/知识库工具规格、用户消息内容。"""

from typing import Any, Callable

from agentscope.message import Base64Source, DataBlock, TextBlock
from agentscope.permission import PermissionMode

from src.agents.factory import (
    AgentGuardrails,
    AgentModelOptions,
    KnowledgeToolRuntimeSpec,
    McpRuntimeSpec,
    SubagentRuntimeContext,
    SubagentRuntimeSpec,
)
from src.security.permissions import build_permission_profile
from src.server.routes.mcp import resolve_mcp_runtime_servers
from src.server.routes.tools import get_enabled_http_tools, get_enabled_knowledge_tools

from src.server.routes.chat.models import ChatImagePayload
from src.server.routes.chat.prompts import _build_agent_memory_prompt
from src.server.routes.skills import build_skill_system_prompt


def _build_user_content(
    text: str,
    images: list[ChatImagePayload],
) -> str | list[TextBlock | DataBlock]:
    """构建 AgentScope 用户消息内容"""
    if not images:
        return text

    blocks: list[TextBlock | DataBlock] = [TextBlock(text=text)]
    for image in images:
        blocks.append(DataBlock(
            name=image.name or None,
            source=Base64Source(
                data=image.data,
                media_type=image.media_type,
            ),
        ))
    return blocks


def _build_runtime_guardrails(agent_config: object | None) -> AgentGuardrails:
    guardrails = getattr(agent_config, "guardrails", None)
    if not guardrails:
        return AgentGuardrails()
    runtime_guardrails = AgentGuardrails(
        filesystem_scope=getattr(guardrails, "filesystem_scope", "/") or "/",
        shell_blacklist=list(getattr(guardrails, "shell_blacklist", []) or []),
        network_enabled=bool(getattr(guardrails, "network_enabled", True)),
        human_approval=bool(getattr(guardrails, "human_approval", False)),
        tool_permission_mode=str(getattr(guardrails, "tool_permission_mode", "accept_edits") or "accept_edits"),
        tool_permission_profile=str(
            getattr(guardrails, "tool_permission_profile", "")
            or _profile_from_legacy_tool_mode(getattr(guardrails, "tool_permission_mode", "accept_edits"))
        ),
        execution_backend=str(getattr(guardrails, "execution_backend", "local") or "local"),
        docker_image=str(getattr(guardrails, "docker_image", "python:3.11-slim") or "python:3.11-slim"),
        persistent_sandbox=bool(getattr(guardrails, "persistent_sandbox", False)),
        timeout_seconds=int(getattr(guardrails, "timeout_seconds", 300) or 300),
    )
    return AgentGuardrails(
        filesystem_scope=runtime_guardrails.filesystem_scope,
        shell_blacklist=runtime_guardrails.shell_blacklist,
        network_enabled=runtime_guardrails.network_enabled,
        human_approval=runtime_guardrails.human_approval,
        tool_permission_mode=runtime_guardrails.tool_permission_mode,
        tool_permission_profile=runtime_guardrails.tool_permission_profile,
        execution_backend=runtime_guardrails.execution_backend,
        docker_image=runtime_guardrails.docker_image,
        persistent_sandbox=runtime_guardrails.persistent_sandbox,
        timeout_seconds=runtime_guardrails.timeout_seconds,
        permission_profile=build_permission_profile(runtime_guardrails),
    )


def _profile_from_legacy_tool_mode(mode: object) -> str:
    value = str(mode or "accept_edits").strip().lower()
    if value == "ask_every_time":
        return "ask_every_time"
    if value == "bypass":
        return "trusted_tools"
    return "confirm_destructive"


def _permission_mode_for_guardrails(guardrails: AgentGuardrails) -> PermissionMode:
    return guardrails.permission_profile.to_agentscope_permission_mode()


def _guardrail_cache_key(guardrails: AgentGuardrails) -> str:
    """生成影响 Agent 实例缓存的 guardrails key。"""

    return (
        f"{guardrails.permission_profile.cache_key()}:"
        f"legacy-mode-{guardrails.tool_permission_mode}:"
        f"legacy-profile-{guardrails.tool_permission_profile}:"
        f"exec-{guardrails.execution_backend}:"
        f"docker-{guardrails.docker_image}:"
        f"persist-sandbox-{guardrails.persistent_sandbox}"
    )


def _build_runtime_mcp_servers(agent_config: object | None) -> list[McpRuntimeSpec]:
    tool_ids = list(getattr(agent_config, "mcp_tools", []) or [])
    specs: list[McpRuntimeSpec] = []
    for item in resolve_mcp_runtime_servers(tool_ids):
        specs.append(McpRuntimeSpec(
            name=str(item.get("name") or ""),
            transport=str(item.get("transport") or ""),
            url=str(item.get("url") or ""),
            command=str(item.get("command") or ""),
            headers=dict(item.get("headers") or {}),
            enable_tools=list(item.get("enable_tools") or []),
        ))
    return specs


def _build_runtime_knowledge_tools(agent_config: object | None) -> list[KnowledgeToolRuntimeSpec]:
    tool_ids = list(getattr(agent_config, "knowledge_tools", []) or [])
    specs: list[KnowledgeToolRuntimeSpec] = []
    http_tool_ids: set[str] = set()
    for item in get_enabled_http_tools(tool_ids):
        http_tool_ids.add(str(item.get("id") or ""))
        specs.append(KnowledgeToolRuntimeSpec(
            id=str(item.get("id") or ""),
            name=str(item.get("name") or "HttpTool"),
            kind="http",
            description=str(item.get("description") or ""),
            config=dict(item),
        ))
    for item in get_enabled_knowledge_tools(tool_ids):
        if str(item.get("id") or "") in http_tool_ids:
            continue
        specs.append(KnowledgeToolRuntimeSpec(
            id=str(item.get("id") or ""),
            name=str(item.get("name") or "KnowledgeBase"),
            kind="legacy_knowledge",
            description=str(item.get("description") or ""),
            url=str(item.get("url") or ""),
            authorization=str(item.get("authorization") or ""),
            headers=dict(item.get("headers") or {}),
            default_payload=dict(item.get("defaultPayload") or item.get("default_payload") or {}),
            parameter_instructions=str(item.get("parameterInstructions") or item.get("parameter_instructions") or ""),
        ))
    return specs


def _build_runtime_subagents(
    agent_config: object | None,
    *,
    selected_subagent_ids: list[str] | None = None,
    max_depth: int | None = None,
    current_depth: int = 0,
    call_stack: list[str] | None = None,
    on_warning: Callable[[dict[str, Any]], None] | None = None,
) -> list[SubagentRuntimeSpec]:
    """根据 Agent 的 callableAgents 构建可注入的子 Agent 运行时规格。"""

    if not agent_config:
        return []
    from src.server.routes.agents import get_registered_agent

    stack = list(call_stack or [])
    parent_id = str(getattr(agent_config, "id", "") or "")
    if parent_id and parent_id not in stack:
        stack.append(parent_id)
    depth_limit = int(max_depth or getattr(agent_config, "max_depth", 2) or 2)
    if current_depth >= depth_limit:
        return []

    selected_ids = set(selected_subagent_ids or []) if current_depth == 0 and selected_subagent_ids is not None else None
    specs: list[SubagentRuntimeSpec] = []
    seen: set[str] = set()
    for child_id in list(getattr(agent_config, "callable_agents", []) or []):
        child_id = str(child_id)
        if not child_id or child_id in seen or child_id in stack:
            continue
        if selected_ids is not None and child_id not in selected_ids:
            continue
        seen.add(child_id)
        child = get_registered_agent(child_id)
        if not child:
            if callable(on_warning):
                on_warning({
                    "eventType": "SUBAGENT_CALL_FAILED",
                    "parentAgentId": parent_id,
                    "parentAgentName": str(getattr(agent_config, "name", "") or ""),
                    "subagentId": child_id,
                    "subagentName": child_id,
                    "task": "",
                    "depth": current_depth + 1,
                    "maxDepth": depth_limit,
                    "status": "blocked",
                    "outputPreview": "",
                    "durationMs": 0,
                    "error": "Configured callable subagent does not exist.",
                    "reason": "missing_subagent",
                })
            continue

        child_model_config = getattr(child, "model_config_data", None)
        child_skill_ids = list(getattr(child, "skills", []) or [])
        child_prompt = "\n\n".join(
            part
            for part in [
                str(getattr(child, "system_prompt", "") or "You are a helpful assistant."),
                _build_agent_memory_prompt(child.id) if bool(getattr(child, "memory_enabled", True)) else "",
                build_skill_system_prompt(child_skill_ids),
            ]
            if part
        )
        specs.append(SubagentRuntimeSpec(
            id=child.id,
            name=child.name,
            description=child.description,
            system_prompt=child_prompt,
            model_name=str(getattr(child_model_config, "primary", "") or ""),
            fallback_names=list(getattr(child_model_config, "fallback", []) or []),
            model_options=AgentModelOptions(
                temperature=getattr(child_model_config, "temperature", None),
                top_p=getattr(child_model_config, "top_p", None),
                max_tokens=getattr(child_model_config, "max_tokens", None),
                max_react_iters=int(getattr(child_model_config, "max_react_iters", 40) or 40),
            ),
            guardrails=_build_runtime_guardrails(child),
            mcp_servers=_build_runtime_mcp_servers(child),
            knowledge_tools=_build_runtime_knowledge_tools(child),
            subagents=_build_runtime_subagents(
                child,
                max_depth=depth_limit,
                current_depth=current_depth + 1,
                call_stack=[*stack, child.id],
                on_warning=on_warning,
            ),
            max_depth=depth_limit,
            memory_enabled=bool(getattr(child, "memory_enabled", True)),
        ))
    return specs


def _build_subagent_context(agent_config: object | None) -> SubagentRuntimeContext:
    """创建一次主 Agent 运行共享的子 Agent 调用上下文。"""

    agent_id = str(getattr(agent_config, "id", "") or "default")
    max_depth = int(getattr(agent_config, "max_depth", 2) or 2)
    return SubagentRuntimeContext(
        root_agent_id=agent_id,
        current_depth=0,
        max_depth=max(1, min(max_depth, 5)),
        call_stack=[agent_id],
        max_calls=8,
    )
