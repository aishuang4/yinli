"""运行时配置构建 — guardrails、权限模式、MCP/知识库工具规格、用户消息内容。"""

from typing import Any
from pathlib import Path

from agentscope.message import Base64Source, DataBlock, TextBlock
from agentscope.permission import PermissionMode

from src.agents.factory import (
    AgentGuardrails,
    KnowledgeToolRuntimeSpec,
    McpRuntimeSpec,
)
from src.security.permissions import build_permission_profile
from src.server.routes.mcp import resolve_mcp_runtime_servers
from src.server.routes.tools import get_enabled_http_tools, get_enabled_knowledge_tools

from src.server.routes.chat.models import ChatImagePayload


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


def scope_guardrails_to_project(
    guardrails: AgentGuardrails,
    project_root: Path,
) -> AgentGuardrails:
    """计算 Agent 文件范围与 Project root 的交集。"""

    project_path = project_root.expanduser().resolve()
    agent_path = Path(guardrails.filesystem_scope).expanduser().resolve()
    if project_path.is_relative_to(agent_path):
        effective_scope = project_path
    elif agent_path.is_relative_to(project_path):
        effective_scope = agent_path
    else:
        raise ValueError(
            f"Agent filesystem scope {agent_path} 与 Project root {project_path} 不相交。",
        )
    return AgentGuardrails(
        filesystem_scope=str(effective_scope),
        shell_blacklist=guardrails.shell_blacklist,
        network_enabled=guardrails.network_enabled,
        human_approval=guardrails.human_approval,
        tool_permission_mode=guardrails.tool_permission_mode,
        tool_permission_profile=guardrails.tool_permission_profile,
        execution_backend=guardrails.execution_backend,
        docker_image=guardrails.docker_image,
        persistent_sandbox=guardrails.persistent_sandbox,
        timeout_seconds=guardrails.timeout_seconds,
        permission_profile=guardrails.permission_profile,
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
        f"scope-{guardrails.filesystem_scope}:"
        f"legacy-mode-{guardrails.tool_permission_mode}:"
        f"legacy-profile-{guardrails.tool_permission_profile}:"
        f"exec-{guardrails.execution_backend}:"
        f"docker-{guardrails.docker_image}:"
        f"persist-sandbox-{guardrails.persistent_sandbox}"
    )


def _build_runtime_mcp_servers(
    agent_config: object | None,
    *,
    allowed_server_ids: set[str] | frozenset[str] | None = None,
) -> list[McpRuntimeSpec]:
    tool_ids = list(getattr(agent_config, "mcp_tools", []) or [])
    specs: list[McpRuntimeSpec] = []
    for item in resolve_mcp_runtime_servers(tool_ids, allowed_server_ids=allowed_server_ids):
        specs.append(McpRuntimeSpec(
            name=str(item.get("name") or ""),
            transport=str(item.get("transport") or ""),
            url=str(item.get("url") or ""),
            command=str(item.get("command") or ""),
            headers=dict(item.get("headers") or {}),
            enable_tools=list(item.get("enable_tools") or []),
        ))
    return specs


def _build_runtime_knowledge_tools(
    agent_config: object | None,
    *,
    allowed_tool_ids: set[str] | frozenset[str] | None = None,
) -> list[KnowledgeToolRuntimeSpec]:
    configured_tool_ids = list(getattr(agent_config, "knowledge_tools", []) or [])
    tool_ids = [
        tool_id
        for tool_id in configured_tool_ids
        if allowed_tool_ids is None or tool_id in allowed_tool_ids
    ]
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
