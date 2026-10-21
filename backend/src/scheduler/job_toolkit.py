"""Job Agent 必注入 / 可选注入工具名单。门禁只能动可选层。"""

from __future__ import annotations

from typing import Any, Iterable, Literal

from pydantic import BaseModel, Field, field_validator

from src.agents.system_agents import is_default_chat_agent_id
from src.scheduler.capability_catalog import CapabilityInventoryItem
from src.scheduler.capability_contract import JobCapabilityClaim, JobCapabilityGap
from src.scheduler.capability_match import _claim_from_row

AgentRole = Literal["chat", "job"]
ToolkitKind = Literal["builtin", "mcp", "http", "knowledge", "skill"]

JOB_BUILTIN_IDS: tuple[str, ...] = ("WebFetch", "HttpRequest", "Bash")
CHAT_ROLE: AgentRole = "chat"
JOB_ROLE: AgentRole = "job"


class JobToolkitItem(BaseModel):
    """一条用户勾选的 Job Agent 工具。"""

    kind: ToolkitKind
    id: str = Field(min_length=1)

    model_config = {"extra": "ignore"}


class JobToolkit(BaseModel):
    """Job Agent 两层工具名单。对话 Agent 为空。"""

    required: list[JobToolkitItem] = Field(default_factory=list)
    optional: list[JobToolkitItem] = Field(default_factory=list)

    model_config = {"extra": "ignore"}

    def is_empty(self) -> bool:
        return not self.required and not self.optional

    def all_items(self) -> list[JobToolkitItem]:
        return [*self.required, *self.optional]


class JobAgentError(ValueError):
    """Job Agent 配置或资格不合法。"""


def empty_job_toolkit() -> JobToolkit:
    return JobToolkit()


def parse_job_toolkit(value: Any) -> JobToolkit:
    if value in (None, "", {}, "null"):
        return empty_job_toolkit()
    if isinstance(value, JobToolkit):
        return value
    if isinstance(value, str):
        import json

        try:
            value = json.loads(value)
        except Exception:
            return empty_job_toolkit()
    if not isinstance(value, dict):
        return empty_job_toolkit()
    return JobToolkit.model_validate(value)


def parse_agent_role(value: Any) -> AgentRole:
    role = str(value or CHAT_ROLE).strip().lower()
    return JOB_ROLE if role == JOB_ROLE else CHAT_ROLE


def require_job_runner(agent: Any, *, agent_id: str = "") -> None:
    """创建 / 启用任务时：执行者必须是配置了必注入工具的 Job Agent。"""

    ident = agent_id or (
        str(agent.get("id") if isinstance(agent, dict) else getattr(agent, "id", "") or "")
    )
    if agent is None:
        raise JobAgentError("找不到要执行定时任务的 Agent")
    if is_default_chat_agent_id(ident) or agent_role_of(agent) != JOB_ROLE:
        raise JobAgentError("定时任务必须选择 Job Agent，对话助手不能到点运行")
    toolkit = job_toolkit_of(agent)
    if not toolkit.required:
        raise JobAgentError("Job Agent 必须配置必注入工具")


def agent_role_of(agent: Any) -> AgentRole:
    if agent is None:
        return CHAT_ROLE
    if isinstance(agent, dict):
        return parse_agent_role(agent.get("role") or agent.get("agentRole"))
    return parse_agent_role(getattr(agent, "role", None))


def job_toolkit_of(agent: Any) -> JobToolkit:
    if agent is None:
        return empty_job_toolkit()
    if isinstance(agent, dict):
        return parse_job_toolkit(agent.get("jobToolkit") or agent.get("job_toolkit"))
    raw = getattr(agent, "job_toolkit", None)
    if raw is None:
        raw = getattr(agent, "jobToolkit", None)
    return parse_job_toolkit(raw)


def _item_key(item: JobToolkitItem) -> tuple[str, str]:
    return (item.kind, item.id)


def validate_job_agent_config(
    *,
    agent_id: str,
    role: AgentRole,
    toolkit: JobToolkit,
    guardrails: Any | None = None,
) -> JobToolkit:
    """保存前校验。对话 Agent 清空 toolkit；Job Agent 必注入不能空。"""

    if is_default_chat_agent_id(agent_id) and role == JOB_ROLE:
        raise JobAgentError("系统默认助手不能改成 Job Agent")
    if role != JOB_ROLE:
        return empty_job_toolkit()

    cleaned = _dedupe_toolkit(toolkit)
    if not cleaned.required:
        raise JobAgentError("Job Agent 必须至少配置一项必注入工具")
    overlap = {_item_key(item) for item in cleaned.required} & {
        _item_key(item) for item in cleaned.optional
    }
    if overlap:
        raise JobAgentError("同一工具不能同时出现在必注入和可选注入名单")
    for item in cleaned.all_items():
        if item.kind == "builtin" and item.id not in JOB_BUILTIN_IDS:
            raise JobAgentError(f"Job Agent 不支持把 {item.id} 配成注入工具")
    network = True
    operation_scope = "normal"
    if guardrails is not None:
        if isinstance(guardrails, dict):
            network = bool(
                guardrails.get("networkEnabled", guardrails.get("network_enabled", True))
            )
            operation_scope = str(
                guardrails.get("operationScope")
                or guardrails.get("operation_scope")
                or ("read_only" if guardrails.get("toolPermissionProfile") == "read_only" or guardrails.get("tool_permission_profile") == "read_only" else "normal")
            )
        else:
            network = bool(getattr(guardrails, "network_enabled", True) if hasattr(guardrails, "network_enabled") else getattr(guardrails, "networkEnabled", True))
            operation_scope = str(getattr(guardrails, "operation_scope", "") or getattr(guardrails, "operationScope", "") or "")
            if not operation_scope and str(getattr(guardrails, "tool_permission_profile", "") or getattr(guardrails, "toolPermissionProfile", "")) == "read_only":
                operation_scope = "read_only"
    if operation_scope == "read_only":
        raise JobAgentError("Job Agent 不能使用只读能力范围")
    needs_network = any(
        item.kind == "builtin" and item.id in {"WebFetch", "HttpRequest"}
        for item in cleaned.required
    ) or any(item.kind in {"mcp", "http"} for item in cleaned.required)
    if needs_network and not network:
        raise JobAgentError("必注入含网络工具时，Job Agent 必须允许网络访问")
    return cleaned


def _dedupe_toolkit(toolkit: JobToolkit) -> JobToolkit:
    def _unique(items: list[JobToolkitItem]) -> list[JobToolkitItem]:
        seen: set[tuple[str, str]] = set()
        result: list[JobToolkitItem] = []
        for item in items:
            key = _item_key(item)
            if key in seen:
                continue
            seen.add(key)
            result.append(item)
        return result

    required = _unique(toolkit.required)
    required_keys = {_item_key(item) for item in required}
    optional = [item for item in _unique(toolkit.optional) if _item_key(item) not in required_keys]
    return JobToolkit(required=required, optional=optional)


def bound_resource_ids(toolkit: JobToolkit) -> dict[str, list[str]]:
    """把两层名单展开成 Agent 绑定字段，供库存 agent_bound 使用。"""

    buckets = {"mcp": [], "http": [], "knowledge": [], "skill": []}
    for item in toolkit.all_items():
        if item.kind in buckets and item.id not in buckets[item.kind]:
            buckets[item.kind].append(item.id)
    return buckets


def prune_job_toolkit(
    toolkit: JobToolkit,
    *,
    mcp_ids: set[str] | None = None,
    knowledge_ids: set[str] | None = None,
    skill_ids: set[str] | None = None,
) -> JobToolkit:
    """资源删除时从两层名单剔除。"""

    def _keep(item: JobToolkitItem) -> bool:
        if mcp_ids and item.kind == "mcp" and item.id in mcp_ids:
            return False
        if knowledge_ids and item.kind in {"http", "knowledge"} and item.id in knowledge_ids:
            return False
        if skill_ids and item.kind == "skill" and item.id in skill_ids:
            return False
        return True

    return JobToolkit(
        required=[item for item in toolkit.required if _keep(item)],
        optional=[item for item in toolkit.optional if _keep(item)],
    )


def find_inventory_item(
    inventory: Iterable[CapabilityInventoryItem],
    item: JobToolkitItem,
) -> CapabilityInventoryItem | None:
    for row in inventory:
        if row.kind != item.kind:
            continue
        if row.resource_id == item.id or row.capability_id.endswith(f":{item.id}"):
            return row
    return None


def resolve_required_layer(
    toolkit: JobToolkit,
    inventory: list[CapabilityInventoryItem],
) -> tuple[list[JobCapabilityClaim], list[JobCapabilityGap]]:
    """第一段：必注入资格。缺一项就是不过，不降级。"""

    claims: list[JobCapabilityClaim] = []
    missing: list[JobCapabilityGap] = []
    for ref in toolkit.required:
        row = find_inventory_item(inventory, ref)
        if row is None or not row.unattended_ok:
            reason = (row.blocked_reason if row is not None else "missing") or "missing"
            missing.append(JobCapabilityGap(
                intent=f"{ref.kind}:{ref.id}",
                reason=reason,
                suggested_action="补齐该 Job Agent 的必注入工具，或改回可用绑定",
            ))
            continue
        claim = _claim_from_row(row)
        claims.append(claim.model_copy(update={"layer": "required"}))
    return claims, missing


def optional_inventory(
    toolkit: JobToolkit,
    inventory: list[CapabilityInventoryItem],
) -> list[CapabilityInventoryItem]:
    """第二段候选：只含可选名单里的工具。"""

    allowed = {_item_key(item) for item in toolkit.optional}
    result: list[CapabilityInventoryItem] = []
    for row in inventory:
        kind = str(row.kind)
        ident = str(row.resource_id)
        if (kind, ident) in allowed:
            result.append(row)
    return result


def intents_covered_by_claims(claims: list[JobCapabilityClaim]) -> set[str]:
    covered: set[str] = set()
    for claim in claims:
        covered.update(claim.tags)
    return covered
