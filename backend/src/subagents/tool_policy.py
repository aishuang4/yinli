"""Subagent 工具继承与裁剪策略。"""

from __future__ import annotations

from dataclasses import dataclass, field
import re

from src.agents.runtime_types import AgentGuardrails, KnowledgeToolRuntimeSpec, McpRuntimeSpec, RuntimeToolScope
from src.security.permissions import ApprovalPolicy
from src.subagents.models import SubagentDefinition
from src.tools.configured_http_tool import safe_http_tool_name


UI_STATE_TOOLS = {
    "AskUser",
    "AskUserQuestion",
    "EnterPlanMode",
    "ExitPlanMode",
    "ScheduleWakeup",
    "WaitForMcpServers",
}
NESTING_TOOLS = {"DelegateTask"}
WRITE_TOOLS = {"Write", "Edit"}
REQUIRED_INTERNAL_TOOLS = {"FinalAnswerStart"}


@dataclass(frozen=True)
class SubagentToolPolicy:
    """Subagent 最终可见工具裁剪结果。"""

    allowed_tool_names: set[str] | None
    denied_tool_names: set[str] = field(default_factory=set)
    effective_tool_names: set[str] = field(default_factory=set)

    def cache_key(self) -> str:
        allowed = "*" if self.allowed_tool_names is None else ",".join(sorted(self.allowed_tool_names))
        denied = ",".join(sorted(self.denied_tool_names))
        effective = ",".join(sorted(self.effective_tool_names))
        return f"allow-{allowed}:deny-{denied}:effective-{effective}"


def build_subagent_tool_policy(
    definition: SubagentDefinition,
    *,
    parent_tool_scope: RuntimeToolScope,
    parent_guardrails: AgentGuardrails,
    child_guardrails: AgentGuardrails | None = None,
    mcp_servers: list[McpRuntimeSpec] | None = None,
    knowledge_tools: list[KnowledgeToolRuntimeSpec] | None = None,
) -> SubagentToolPolicy:
    """按“继承主会话工具池，再用 frontmatter 裁剪”的语义计算工具策略。"""

    base = _parent_available_tools(
        parent_tool_scope,
        parent_guardrails,
        mcp_servers=mcp_servers,
        knowledge_tools=knowledge_tools,
    )
    if child_guardrails is not None:
        base &= _parent_available_tools(
            parent_tool_scope,
            child_guardrails,
            mcp_servers=mcp_servers,
            knowledge_tools=knowledge_tools,
        )
    denied = _expand_patterns(definition.disallowed_tools, base)
    denied.update(UI_STATE_TOOLS)
    denied.update(NESTING_TOOLS)
    denied.difference_update(REQUIRED_INTERNAL_TOOLS)

    after_deny = base - denied
    if definition.tools is None:
        effective = set(after_deny)
        allowed: set[str] | None = None
    else:
        allowed = _expand_patterns(definition.tools, after_deny)
        effective = _intersect_tools(after_deny, allowed)
    effective.update(base & REQUIRED_INTERNAL_TOOLS)

    return SubagentToolPolicy(
        allowed_tool_names=effective,
        denied_tool_names=denied,
        effective_tool_names=effective,
    )


def _parent_available_tools(
    tool_scope: RuntimeToolScope,
    guardrails: AgentGuardrails,
    *,
    mcp_servers: list[McpRuntimeSpec] | None = None,
    knowledge_tools: list[KnowledgeToolRuntimeSpec] | None = None,
) -> set[str]:
    permission_profile = guardrails.permission_profile
    read_only = permission_profile.tool_policy == ApprovalPolicy.READ_ONLY
    tools = {
        "Read",
        "Grep",
        "Glob",
        "AskUser",
        "FinalAnswerStart",
        "memory_node_tree",
        "memory_node_query",
    }
    if not read_only:
        tools.update(WRITE_TOOLS)
    if permission_profile.can_execute and tool_scope.allow_bash:
        tools.add("Bash")
    if permission_profile.can_use_network:
        if tool_scope.allow_http:
            tools.add("HttpRequest")
        if tool_scope.allow_web:
            tools.add("WebFetch")
        if tool_scope.allow_knowledge:
            tools.update(_knowledge_tool_names(knowledge_tools or []))
        if tool_scope.allow_mcp:
            tools.update(_mcp_tool_names(mcp_servers or []))
    return tools


def _expand_patterns(patterns: list[str], base: set[str]) -> set[str]:
    expanded: set[str] = set()
    for pattern in patterns:
        if pattern == "mcp__*":
            expanded.update(tool for tool in base if tool.startswith("mcp__"))
            continue
        if pattern.startswith("mcp__") and (pattern.endswith("__*") or pattern.count("__") == 1):
            server = pattern.removesuffix("__*")
            expanded.update(tool for tool in base if tool == server or tool.startswith(f"{server}__"))
            continue
        expanded.add(pattern)
    return expanded


def _intersect_tools(base: set[str], allowed: set[str]) -> set[str]:
    effective = base & allowed
    server_wildcards = {
        tool.removesuffix("__*")
        for tool in base
        if tool.startswith("mcp__") and tool.endswith("__*")
    }
    for tool in allowed:
        if not tool.startswith("mcp__") or tool.endswith("__*"):
            continue
        server = "__".join(tool.split("__")[:2])
        if server in server_wildcards:
            effective.add(tool)
    return effective


def _knowledge_tool_names(knowledge_tools: list[KnowledgeToolRuntimeSpec]) -> set[str]:
    names: set[str] = set()
    for tool in knowledge_tools:
        if tool.kind == "http":
            names.add(safe_http_tool_name(tool.name or "Custom"))
            continue
        safe_name = re.sub(r"[^a-zA-Z0-9_]", "_", tool.name or "").strip("_") or "default"
        names.add((safe_name if safe_name == "KnowledgeBaseSearch" else f"KnowledgeBaseSearch_{safe_name}")[:64])
    return names


def _mcp_tool_names(mcp_servers: list[McpRuntimeSpec]) -> set[str]:
    names: set[str] = set()
    for server in mcp_servers:
        server_name = str(server.name or "").strip()
        if not server_name:
            continue
        if not server.enable_tools:
            names.add(f"mcp__{server_name}__*")
            continue
        for tool_name in server.enable_tools:
            if str(tool_name).strip():
                names.add(f"mcp__{server_name}__{tool_name}")
    return names
