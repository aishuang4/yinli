"""到期 toolkit 装配：空合同走 Phase 1 五旗；非空合同按 claimed ∩ live。"""

from __future__ import annotations

from typing import Any

from src.scheduler.capability_contract import parse_capability_contract
from src.scheduler.http_runtime import enabled_http_tools, enabled_knowledge_tools
from src.scheduler.mcp_runtime import resolve_mcp_runtime_servers
from src.scheduler.models import JobToolScope, ScheduledJob
from src.scheduler.unattended_policy import agent_approval_policy
from src.security.permissions import ApprovalPolicy
from src.storage.database import list_http_tools, list_json_records, list_mcp_servers

KNOWLEDGE_TOOLS_NAMESPACE = "knowledge_tools"
ALWAYS_PREVIEW = [
    "Read", "Grep", "Glob", "Write", "Edit",
    "FinalAnswerStart", "MemoryNodeTree", "MemoryNodeQuery",
]


class CapabilityUnboundError(ValueError):
    """合同认领的工具现场已不可用。"""


def assemble_scheduled_firing_toolkit_spec(
    job: ScheduledJob | Any,
    *,
    agent: Any | None = None,
    project_resources: Any | None = None,
) -> dict[str, Any]:
    """纯函数，不启动 Run。缺资源抛 CapabilityUnboundError。"""

    contract = parse_capability_contract(getattr(job, "capability_contract", None))
    scope = getattr(job, "tool_scope", None)
    if not isinstance(scope, JobToolScope):
        scope = JobToolScope.model_validate(scope or {})
    if contract.is_generate_only() or contract.verdict == "generate_only":
        tool_scope = JobToolScope(
            allow_bash=bool(scope.allow_bash),
            allow_http=False,
            allow_web=False,
            allow_mcp=False,
            allow_knowledge=False,
        )
        preview = list(ALWAYS_PREVIEW)
        if tool_scope.allow_bash:
            preview.append("Bash")
        return {
            "tool_scope": tool_scope.model_dump(),
            "mcp_ids": [],
            "http_ids": [],
            "knowledge_ids": [],
            "enable_tools": [],
            "filter_claimed": False,
            "denylist_only": True,
            "legacy_tool_scope_ignored": bool(
                scope.allow_mcp or scope.allow_http or scope.allow_web or scope.allow_knowledge
            ),
            "firing_tools_preview": preview,
        }

    if agent is None:
        from src.server.routes.agents import get_registered_agent

        agent = get_registered_agent(str(getattr(job, "agent_id", "") or ""))
    policy = agent_approval_policy(agent)
    trusted = policy == ApprovalPolicy.TRUSTED_TOOLS
    agent_mcp = {str(item) for item in list(getattr(agent, "mcp_tools", None) or [])}
    agent_http = {str(item) for item in list(getattr(agent, "knowledge_tools", None) or [])}
    allowed_mcp = getattr(project_resources, "mcp_server_ids", None)
    allowed_http = getattr(project_resources, "http_tool_ids", None)

    claimed_mcp = {c.resource_id for c in contract.claimed if c.kind == "mcp"}
    claimed_http = {c.resource_id for c in contract.claimed if c.kind == "http"}
    claimed_knowledge = {c.resource_id for c in contract.claimed if c.kind == "knowledge"}

    live_mcp_ids = _live_mcp_resource_ids(
        claimed_mcp, agent_mcp, allowed_server_ids=allowed_mcp,
    )
    missing_mcp = sorted(claimed_mcp - live_mcp_ids)
    if missing_mcp:
        raise CapabilityUnboundError("capability_unbound: " + ", ".join(missing_mcp))
    servers = resolve_mcp_runtime_servers(
        list(live_mcp_ids),
        list_mcp_servers(),
        allowed_server_ids=allowed_mcp,
    )
    enable_tools: list[str] = []
    for server in servers:
        enable_tools.extend(str(name) for name in list(server.get("enable_tools") or []))

    http_records = enabled_http_tools(list(agent_http & claimed_http), list_http_tools(), allowed_ids=allowed_http)
    live_http = {str(item.get("id") or "") for item in http_records}
    missing_http = sorted(claimed_http - live_http)
    if missing_http:
        raise CapabilityUnboundError("capability_unbound: " + ", ".join(missing_http))

    knowledge_records = enabled_knowledge_tools(
        list(agent_http & claimed_knowledge),
        list_json_records(KNOWLEDGE_TOOLS_NAMESPACE),
        live_http,
        allowed_ids=allowed_http,
    )
    live_knowledge = {str(item.get("id") or "") for item in knowledge_records}
    missing_knowledge = sorted(claimed_knowledge - live_knowledge)
    if missing_knowledge:
        raise CapabilityUnboundError("capability_unbound: " + ", ".join(missing_knowledge))

    tool_scope = JobToolScope(
        allow_mcp=bool(servers),
        allow_knowledge=bool(live_http or live_knowledge),
        allow_http=bool(contract.allow_http_request and trusted),
        allow_web=bool(contract.allow_web_fetch and trusted),
        allow_bash=bool(contract.allow_bash and trusted),
    )
    preview = list(ALWAYS_PREVIEW)
    if tool_scope.allow_bash:
        preview.append("Bash")
    if tool_scope.allow_http:
        preview.append("HttpRequest")
    if tool_scope.allow_web:
        preview.append("WebFetch")
    for item in contract.claimed:
        if item.kind != "builtin" and item.tool_name and item.tool_name not in preview:
            preview.append(item.tool_name)
    return {
        "tool_scope": tool_scope.model_dump(),
        "mcp_ids": sorted(claimed_mcp),
        "http_ids": sorted(live_http),
        "knowledge_ids": sorted(live_knowledge),
        "enable_tools": enable_tools,
        "filter_claimed": True,
        "denylist_only": True,
        "legacy_tool_scope_ignored": False,
        "firing_tools_preview": preview,
    }


def derived_tool_scope_from_spec(spec: dict[str, Any]) -> JobToolScope:
    return JobToolScope.model_validate(spec.get("tool_scope") or {})


def _live_mcp_resource_ids(
    claimed_ids: set[str],
    agent_mcp: set[str],
    *,
    allowed_server_ids: set[str] | frozenset[str] | None,
) -> set[str]:
    live: set[str] = set()
    for server in list_mcp_servers():
        server_id = str(server.get("id") or "")
        if allowed_server_ids is not None and server_id not in allowed_server_ids:
            continue
        if str(server.get("status") or "") != "installed" or not server.get("enabled"):
            continue
        for tool in list(server.get("tools") or []):
            if isinstance(tool, dict):
                tool_id = str(tool.get("id") or "")
            else:
                tool_id = str(getattr(tool, "id", "") or "")
            if tool_id and tool_id in claimed_ids and tool_id in agent_mcp:
                live.add(tool_id)
    return live
