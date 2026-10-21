"""按 Agent 组装无人值守能力库存。MCP 与 HTTP 是租户全局，不再按 Project 过滤。禁止 import routes。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Literal

from pydantic import BaseModel, Field

from src.scheduler.capability_match import tag_inventory_row
from src.scheduler.http_runtime import enabled_http_tools, enabled_knowledge_tools
from src.scheduler.mcp_runtime import mcp_injectable
from src.scheduler.unattended_policy import agent_approval_policy, skill_package_has_scripts
from src.security.permissions import ApprovalPolicy, UnifiedPermissionProfile, build_permission_profile
from src.storage.database import (
    list_http_tools,
    list_json_records,
    list_mcp_servers,
    list_skills,
)

KNOWLEDGE_TOOLS_NAMESPACE = "knowledge_tools"
_FILE_BUILTINS = ("Read", "Grep", "Glob", "Write", "Edit")
_NETWORK_BUILTINS = ("HttpRequest", "WebFetch")


class CapabilityInventoryItem(BaseModel):
    """一条可注入、可打标的工具库存。"""

    capability_id: str
    kind: Literal["builtin", "mcp", "http", "knowledge", "skill"]
    resource_id: str
    server_id: str = ""
    tool_name: str
    title: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    tag_source: dict[str, str] = Field(default_factory=dict)
    unattended_ok: bool
    blocked_reason: str = ""
    project_bound: bool = True
    agent_bound: bool = True


def build_agent_capability_inventory(agent_id: str) -> list[CapabilityInventoryItem]:
    """只读组装库存。Agent 通过 lazy import 获取。不读 Project 目录下的 MCP。"""

    from src.server.routes.agents import get_registered_agent

    agent = get_registered_agent(agent_id) if agent_id != "default" else get_registered_agent("default")
    if agent_id != "default" and agent is None:
        return []
    profile = build_permission_profile(getattr(agent, "guardrails", None) if agent else None)
    policy = agent_approval_policy(agent)
    items: list[CapabilityInventoryItem] = []
    items.extend(_builtin_rows(profile, policy))
    items.extend(_mcp_rows(agent, profile, policy))
    http_rows, http_ids = _http_rows(agent, profile, policy)
    items.extend(http_rows)
    items.extend(_knowledge_rows(agent, http_ids, profile, policy))
    items.extend(_skill_rows(agent, profile, policy))
    return items


def _builtin_rows(
    profile: UnifiedPermissionProfile,
    policy: ApprovalPolicy,
) -> list[CapabilityInventoryItem]:
    rows: list[CapabilityInventoryItem] = []
    for name in (*_FILE_BUILTINS, "Bash", *_NETWORK_BUILTINS):
        ok, reason = _builtin_unattended(name, profile, policy)
        tags, tag_source = tag_inventory_row(
            kind="builtin",
            tool_name=name,
            title=name,
            description="",
        )
        rows.append(CapabilityInventoryItem(
            capability_id=f"builtin:{name}",
            kind="builtin",
            resource_id=name,
            tool_name=name,
            title=name,
            tags=tags,
            tag_source=tag_source,
            unattended_ok=ok,
            blocked_reason=reason,
        ))
    return rows


def _builtin_unattended(
    name: str,
    profile: UnifiedPermissionProfile,
    policy: ApprovalPolicy,
) -> tuple[bool, str]:
    if name in {"Read", "Grep", "Glob"}:
        return True, ""
    if name in {"Write", "Edit"}:
        if profile.is_read_only:
            return False, "read_only"
        return True, ""
    if profile.is_read_only:
        return False, "read_only"
    if policy != ApprovalPolicy.TRUSTED_TOOLS:
        return False, "blocked_by_permission"
    if name in _NETWORK_BUILTINS and not profile.can_use_network:
        return False, "network_disabled"
    return True, ""


def _mcp_rows(
    agent: Any,
    profile: UnifiedPermissionProfile,
    policy: ApprovalPolicy,
) -> list[CapabilityInventoryItem]:
    selected = {str(item) for item in list(getattr(agent, "mcp_tools", None) or []) if str(item).strip()}
    rows: list[CapabilityInventoryItem] = []
    for server in list_mcp_servers():
        server_id = str(server.get("id") or "")
        if not server_id:
            continue
        server_tags = [str(item) for item in list(server.get("tags") or [])]
        for tool in list(server.get("tools") or []):
            if not isinstance(tool, dict):
                tool_id = str(getattr(tool, "id", "") or "")
                tool_name = str(getattr(tool, "name", "") or tool_id)
                description = str(getattr(tool, "description", "") or "")
            else:
                tool_id = str(tool.get("id") or "")
                tool_name = str(tool.get("name") or tool_id)
                description = str(tool.get("description") or "")
            if not tool_id:
                continue
            agent_bound = tool_id in selected
            tags, tag_source = tag_inventory_row(
                kind="mcp",
                tool_name=tool_name,
                title=tool_name,
                description=description,
                declared_tags=server_tags,
            )
            ok, reason = _channel_unattended(
                kind="mcp",
                profile=profile,
                policy=policy,
                transport=str(server.get("transport") or ""),
                url=str(server.get("url") or ""),
                approval_policy="",
                installed=str(server.get("status") or "") == "installed" and bool(server.get("enabled")),
            )
            if not agent_bound:
                ok, reason = False, reason or "agent_unbound"
            rows.append(CapabilityInventoryItem(
                capability_id=f"mcp:{tool_id}",
                kind="mcp",
                resource_id=tool_id,
                server_id=server_id,
                tool_name=tool_name,
                title=str(server.get("name") or tool_name),
                description=description,
                tags=tags,
                tag_source=tag_source,
                unattended_ok=ok,
                blocked_reason=reason,
                project_bound=True,
                agent_bound=agent_bound,
            ))
    return rows


def _http_rows(
    agent: Any,
    profile: UnifiedPermissionProfile,
    policy: ApprovalPolicy,
) -> tuple[list[CapabilityInventoryItem], set[str]]:
    selected = [str(item) for item in list(getattr(agent, "knowledge_tools", None) or [])]
    rows: list[CapabilityInventoryItem] = []
    http_ids: set[str] = set()
    for item in enabled_http_tools(selected, list_http_tools()):
        tool_id = str(item.get("id") or "")
        if not tool_id:
            continue
        http_ids.add(tool_id)
        tags, tag_source = tag_inventory_row(
            kind="http",
            tool_name=str(item.get("name") or tool_id),
            title=str(item.get("name") or tool_id),
            description=str(item.get("description") or ""),
            declared_tags=[str(item.get("category") or "")],
        )
        ok, reason = _channel_unattended(
            kind="http",
            profile=profile,
            policy=policy,
            transport="",
            url=str(item.get("url") or ""),
            approval_policy=str(item.get("approvalPolicy") or item.get("approval_policy") or "inherit"),
            installed=bool(item.get("enabled", True)),
        )
        rows.append(CapabilityInventoryItem(
            capability_id=f"http:{tool_id}",
            kind="http",
            resource_id=tool_id,
            tool_name=str(item.get("name") or tool_id),
            title=str(item.get("name") or tool_id),
            description=str(item.get("description") or ""),
            tags=tags,
            tag_source=tag_source,
            unattended_ok=ok,
            blocked_reason=reason,
            project_bound=True,
            agent_bound=True,
        ))
    return rows, http_ids


def _knowledge_rows(
    agent: Any,
    http_ids: set[str],
    profile: UnifiedPermissionProfile,
    policy: ApprovalPolicy,
) -> list[CapabilityInventoryItem]:
    selected = [str(item) for item in list(getattr(agent, "knowledge_tools", None) or [])]
    records = enabled_knowledge_tools(
        selected,
        list_json_records(KNOWLEDGE_TOOLS_NAMESPACE),
        http_ids,
        allowed_ids=None,
    )
    rows: list[CapabilityInventoryItem] = []
    for item in records:
        tool_id = str(item.get("id") or "")
        if not tool_id:
            continue
        tags, tag_source = tag_inventory_row(
            kind="knowledge",
            tool_name=str(item.get("name") or tool_id),
            title=str(item.get("name") or tool_id),
            description=str(item.get("description") or ""),
            declared_tags=["knowledge.search"],
        )
        ok, reason = _channel_unattended(
            kind="knowledge",
            profile=profile,
            policy=policy,
            transport="",
            url=str(item.get("url") or ""),
            approval_policy="inherit",
            installed=bool(item.get("enabled", True)),
        )
        rows.append(CapabilityInventoryItem(
            capability_id=f"knowledge:{tool_id}",
            kind="knowledge",
            resource_id=tool_id,
            tool_name=str(item.get("name") or tool_id),
            title=str(item.get("name") or tool_id),
            description=str(item.get("description") or ""),
            tags=tags,
            tag_source=tag_source,
            unattended_ok=ok,
            blocked_reason=reason,
            project_bound=True,
            agent_bound=True,
        ))
    return rows


def _skill_rows(
    agent: Any,
    profile: UnifiedPermissionProfile,
    policy: ApprovalPolicy,
) -> list[CapabilityInventoryItem]:
    selected = {str(item) for item in list(getattr(agent, "skills", None) or [])}
    rows: list[CapabilityInventoryItem] = []
    for item in list_skills():
        skill_id = str(item.get("id") or "")
        if not skill_id or skill_id not in selected:
            continue
        if str(item.get("status") or "") != "installed" or not bool(item.get("enabled", True)):
            continue
        declared = [str(tag) for tag in list(item.get("tags") or [])]
        if skill_package_has_scripts(SimpleNamespace(
            resource_path=item.get("resourcePath") or item.get("resource_path") or "",
            resources=item.get("resources") or [],
        )):
            declared.append("shell.execute")
        tags, tag_source = tag_inventory_row(
            kind="skill",
            tool_name=str(item.get("name") or skill_id),
            title=str(item.get("name") or skill_id),
            description=str(item.get("description") or ""),
            declared_tags=declared,
        )
        ok = True
        reason = ""
        if "shell.execute" in tags and policy != ApprovalPolicy.TRUSTED_TOOLS:
            ok, reason = False, "blocked_by_permission"
        if profile.is_read_only:
            ok, reason = False, "read_only"
        rows.append(CapabilityInventoryItem(
            capability_id=f"skill:{skill_id}",
            kind="skill",
            resource_id=skill_id,
            tool_name=str(item.get("name") or skill_id),
            title=str(item.get("name") or skill_id),
            description=str(item.get("description") or ""),
            tags=tags,
            tag_source=tag_source,
            unattended_ok=ok,
            blocked_reason=reason,
            project_bound=True,
            agent_bound=True,
        ))
    return rows


def _channel_unattended(
    *,
    kind: str,
    profile: UnifiedPermissionProfile,
    policy: ApprovalPolicy,
    transport: str,
    url: str,
    approval_policy: str,
    installed: bool,
) -> tuple[bool, str]:
    if not installed:
        return False, "not_installed"
    if profile.is_read_only:
        return False, "read_only"
    if policy != ApprovalPolicy.TRUSTED_TOOLS:
        return False, "blocked_by_permission"
    if kind in {"mcp", "http", "knowledge"} and not profile.can_use_network:
        return False, "network_disabled"
    if kind == "mcp":
        if str(transport or "").strip().lower() == "stdio":
            return False, "stdio_mcp_not_injectable"
        if not mcp_injectable(transport, url):
            return False, "mcp_not_injectable"
    policy_name = str(approval_policy or "inherit").strip().lower()
    if kind == "http":
        if policy_name == "deny":
            return False, "http_denied"
        if policy_name == "ask":
            return False, "http_ask"
        if policy_name in {"inherit", "allow"} and policy != ApprovalPolicy.TRUSTED_TOOLS:
            return False, "blocked_by_permission"
    return True, ""
