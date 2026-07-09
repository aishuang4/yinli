"""Subagent permissionMode 解析与父权限合并。

Subagent 可以通过 frontmatter 的 `permissionMode` 收窄自身权限，但不能突破父
Agent 当前 guardrails。这里同时输出 AgentScope permission mode 和本项目统一
权限配置，保证工具注入、审计和 AgentScope 执行语义一致。
"""

from __future__ import annotations

from dataclasses import dataclass

from agentscope.permission import PermissionMode

from src.agents.runtime_types import AgentGuardrails
from src.security.permissions import ApprovalPolicy, UnifiedPermissionProfile, legacy_mode_from_policy
from src.subagents.models import SubagentDefinition


_POLICY_RANK = {
    ApprovalPolicy.READ_ONLY: 0,
    ApprovalPolicy.ASK_EVERY_TIME: 1,
    ApprovalPolicy.CONFIRM_DESTRUCTIVE: 2,
    ApprovalPolicy.TRUSTED_TOOLS: 3,
}


@dataclass(frozen=True)
class SubagentPermissionResolution:
    """Subagent permissionMode 解析结果。"""

    requested_mode: str
    normalized_mode: str
    effective_mode: str
    parent_policy: ApprovalPolicy
    requested_policy: ApprovalPolicy
    effective_policy: ApprovalPolicy
    agentscope_mode: PermissionMode
    clamped_by_parent: bool
    guardrails: AgentGuardrails

    def audit_payload(self) -> dict:
        """生成写入 Runtime Event 的权限摘要。"""

        return {
            "requestedPermissionMode": self.requested_mode,
            "normalizedPermissionMode": self.normalized_mode,
            "effectivePermissionMode": self.effective_mode,
            "parentToolPolicy": self.parent_policy.value,
            "requestedToolPolicy": self.requested_policy.value,
            "effectiveToolPolicy": self.effective_policy.value,
            "agentScopePermissionMode": self.agentscope_mode.value,
            "clampedByParent": self.clamped_by_parent,
        }


def resolve_subagent_permission(
    definition: SubagentDefinition,
    parent_guardrails: AgentGuardrails,
) -> SubagentPermissionResolution:
    """解析 Subagent permissionMode，并与父 Agent guardrails 做安全合并。"""

    requested_mode = str(definition.permission_mode or "default").strip() or "default"
    normalized_mode = normalize_subagent_permission_mode(requested_mode)
    parent_profile = parent_guardrails.permission_profile
    parent_policy = _effective_parent_policy(parent_profile)
    requested_policy = _requested_policy_for_mode(normalized_mode, parent_policy)
    effective_policy = _stricter_policy(parent_policy, requested_policy)
    clamped = effective_policy != requested_policy
    agentscope_mode = _agentscope_mode_for_effective_policy(
        normalized_mode=normalized_mode,
        effective_policy=effective_policy,
        parent_profile=parent_profile,
    )
    effective_mode = _effective_mode_label(normalized_mode, effective_policy, agentscope_mode)
    guardrails = _child_guardrails(
        parent_guardrails=parent_guardrails,
        effective_policy=effective_policy,
        parent_profile=parent_profile,
    )
    return SubagentPermissionResolution(
        requested_mode=requested_mode,
        normalized_mode=normalized_mode,
        effective_mode=effective_mode,
        parent_policy=parent_policy,
        requested_policy=requested_policy,
        effective_policy=effective_policy,
        agentscope_mode=agentscope_mode,
        clamped_by_parent=clamped,
        guardrails=guardrails,
    )


def normalize_subagent_permission_mode(value: str) -> str:
    """把 Claude 风格 permissionMode 和常见别名归一为规范值。"""

    normalized = value.strip()
    aliases = {
        "accept_edits": "acceptEdits",
        "accept-edits": "acceptEdits",
        "dont_ask": "dontAsk",
        "dont-ask": "dontAsk",
        "bypass": "bypassPermissions",
        "bypass_permissions": "bypassPermissions",
        "bypass-permissions": "bypassPermissions",
        "explore": "plan",
    }
    key = normalized.lower()
    for alias, canonical in aliases.items():
        if key == alias.lower():
            return canonical
    if key in {"default", "acceptedits", "auto", "dontask", "bypasspermissions", "plan"}:
        return {
            "default": "default",
            "acceptedits": "acceptEdits",
            "auto": "auto",
            "dontask": "dontAsk",
            "bypasspermissions": "bypassPermissions",
            "plan": "plan",
        }[key]
    return "default"


def _effective_parent_policy(profile: UnifiedPermissionProfile) -> ApprovalPolicy:
    if profile.is_read_only:
        return ApprovalPolicy.READ_ONLY
    if profile.human_approval:
        return ApprovalPolicy.ASK_EVERY_TIME
    return profile.tool_policy


def _requested_policy_for_mode(normalized_mode: str, parent_policy: ApprovalPolicy) -> ApprovalPolicy:
    if normalized_mode == "default":
        return parent_policy
    if normalized_mode == "plan":
        return ApprovalPolicy.READ_ONLY
    if normalized_mode == "dontAsk":
        return ApprovalPolicy.ASK_EVERY_TIME
    if normalized_mode in {"acceptEdits", "auto"}:
        return ApprovalPolicy.CONFIRM_DESTRUCTIVE
    if normalized_mode == "bypassPermissions":
        return ApprovalPolicy.TRUSTED_TOOLS
    return parent_policy


def _stricter_policy(parent_policy: ApprovalPolicy, requested_policy: ApprovalPolicy) -> ApprovalPolicy:
    return parent_policy if _POLICY_RANK[parent_policy] < _POLICY_RANK[requested_policy] else requested_policy


def _agentscope_mode_for_effective_policy(
    *,
    normalized_mode: str,
    effective_policy: ApprovalPolicy,
    parent_profile: UnifiedPermissionProfile,
) -> PermissionMode:
    if effective_policy == ApprovalPolicy.READ_ONLY:
        return PermissionMode.EXPLORE
    if effective_policy == ApprovalPolicy.ASK_EVERY_TIME:
        if normalized_mode == "dontAsk" and not parent_profile.human_approval:
            return PermissionMode.DONT_ASK
        return PermissionMode.DEFAULT
    if effective_policy == ApprovalPolicy.CONFIRM_DESTRUCTIVE:
        return PermissionMode.ACCEPT_EDITS
    if effective_policy == ApprovalPolicy.TRUSTED_TOOLS:
        return PermissionMode.BYPASS if normalized_mode == "bypassPermissions" else PermissionMode.ACCEPT_EDITS
    return PermissionMode.DEFAULT


def _effective_mode_label(
    normalized_mode: str,
    effective_policy: ApprovalPolicy,
    agentscope_mode: PermissionMode,
) -> str:
    if effective_policy == ApprovalPolicy.READ_ONLY:
        return "plan"
    if agentscope_mode == PermissionMode.DONT_ASK:
        return "dontAsk"
    if effective_policy == ApprovalPolicy.ASK_EVERY_TIME:
        return "default"
    if normalized_mode == "bypassPermissions" and effective_policy == ApprovalPolicy.TRUSTED_TOOLS:
        return "bypassPermissions"
    if normalized_mode == "auto" and effective_policy == ApprovalPolicy.CONFIRM_DESTRUCTIVE:
        return "auto"
    if effective_policy == ApprovalPolicy.CONFIRM_DESTRUCTIVE:
        return "acceptEdits"
    return normalized_mode


def _child_guardrails(
    *,
    parent_guardrails: AgentGuardrails,
    effective_policy: ApprovalPolicy,
    parent_profile: UnifiedPermissionProfile,
) -> AgentGuardrails:
    child_profile = UnifiedPermissionProfile(
        filesystem_scope=parent_profile.filesystem_scope,
        network_enabled=parent_profile.network_enabled,
        human_approval=parent_profile.human_approval and effective_policy != ApprovalPolicy.READ_ONLY,
        tool_policy=effective_policy,
        shell_blacklist=parent_profile.shell_blacklist,
        timeout_seconds=parent_profile.timeout_seconds,
        owner=parent_profile.owner,
        workspace=parent_profile.workspace,
        role=parent_profile.role,
    )
    return AgentGuardrails(
        filesystem_scope=parent_guardrails.filesystem_scope,
        shell_blacklist=list(parent_guardrails.shell_blacklist),
        network_enabled=parent_guardrails.network_enabled,
        human_approval=child_profile.human_approval,
        tool_permission_mode=legacy_mode_from_policy(effective_policy),
        tool_permission_profile=effective_policy.value,
        execution_backend=parent_guardrails.execution_backend,
        docker_image=parent_guardrails.docker_image,
        persistent_sandbox=parent_guardrails.persistent_sandbox,
        timeout_seconds=parent_guardrails.timeout_seconds,
        permission_profile=child_profile,
    )
