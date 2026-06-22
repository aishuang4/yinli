"""统一资源权限模型。

本模块把旧的 Agent guardrails 字段归一化为统一权限配置，供运行时、
工具注入、AgentScope permission mode 和后续审计共用。
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from agentscope.permission import PermissionMode


class ResourceType(StrEnum):
    """平台内可授权资源类型。"""

    AGENT = "agent"
    TEAM = "team"
    TOOL = "tool"
    SKILL = "skill"
    MCP = "mcp"
    KNOWLEDGE_TOOL = "knowledge_tool"
    WORKSPACE = "workspace"
    RUN = "run"


class PermissionAction(StrEnum):
    """平台内资源动作。"""

    READ = "read"
    EXECUTE = "execute"
    WRITE = "write"
    DELETE = "delete"
    CONFIGURE = "configure"
    APPROVE = "approve"


class PermissionDecision(StrEnum):
    """统一权限决策结果。"""

    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"
    INHERIT = "inherit"


class ApprovalPolicy(StrEnum):
    """工具执行审批策略。"""

    READ_ONLY = "read_only"
    TRUSTED_TOOLS = "trusted_tools"
    CONFIRM_DESTRUCTIVE = "confirm_destructive"
    ASK_EVERY_TIME = "ask_every_time"


@dataclass(frozen=True)
class UnifiedPermissionProfile:
    """运行期统一权限配置。

    第一版保持与现有 guardrails 字段兼容，不改变前端保存结构。
    后续可以把本结构直接持久化到权限表或策略表。
    """

    filesystem_scope: str = "/"
    network_enabled: bool = True
    human_approval: bool = False
    tool_policy: ApprovalPolicy = ApprovalPolicy.CONFIRM_DESTRUCTIVE
    shell_blacklist: tuple[str, ...] = field(default_factory=lambda: ("rm -rf /", "sudo"))
    timeout_seconds: int = 300
    owner: str = "local-user"
    workspace: str = "default"
    role: str = "owner"

    @property
    def is_read_only(self) -> bool:
        """是否只允许只读能力。"""

        return self.tool_policy == ApprovalPolicy.READ_ONLY

    @property
    def can_use_network(self) -> bool:
        """是否允许网络类工具。"""

        return self.network_enabled and not self.is_read_only

    def decide(self, resource_type: ResourceType, action: PermissionAction) -> PermissionDecision:
        """根据统一策略给出基础决策。

        这里只做策略级决策，具体工具的危险参数仍由工具自己的
        `check_permissions` 继续处理。
        """

        if self.is_read_only and action in {
            PermissionAction.WRITE,
            PermissionAction.DELETE,
            PermissionAction.EXECUTE,
            PermissionAction.CONFIGURE,
        }:
            return PermissionDecision.DENY

        if resource_type in {ResourceType.MCP, ResourceType.KNOWLEDGE_TOOL} and not self.can_use_network:
            return PermissionDecision.DENY

        if self.human_approval or self.tool_policy == ApprovalPolicy.ASK_EVERY_TIME:
            return PermissionDecision.ASK

        if self.tool_policy == ApprovalPolicy.TRUSTED_TOOLS:
            return PermissionDecision.ALLOW

        if self.tool_policy == ApprovalPolicy.CONFIRM_DESTRUCTIVE and action in {
            PermissionAction.WRITE,
            PermissionAction.DELETE,
            PermissionAction.EXECUTE,
        }:
            return PermissionDecision.ASK

        return PermissionDecision.ALLOW

    def to_agentscope_permission_mode(self) -> PermissionMode:
        """映射到 AgentScope 当前支持的 permission mode。"""

        if self.human_approval or self.tool_policy == ApprovalPolicy.ASK_EVERY_TIME:
            return PermissionMode.DEFAULT
        if self.tool_policy in {ApprovalPolicy.TRUSTED_TOOLS, ApprovalPolicy.READ_ONLY}:
            return PermissionMode.BYPASS
        return PermissionMode.ACCEPT_EDITS

    def cache_key(self) -> str:
        """生成影响 Agent 运行时缓存的稳定 key。"""

        return (
            f"scope-{self.filesystem_scope}:net-{self.network_enabled}:"
            f"hitl-{self.human_approval}:policy-{self.tool_policy.value}:"
            f"timeout-{self.timeout_seconds}:blacklist-{','.join(self.shell_blacklist)}"
        )

    def audit_payload(self) -> dict[str, Any]:
        """生成可写入运行轨迹或审计记录的安全摘要。"""

        return {
            "filesystemScope": self.filesystem_scope,
            "networkEnabled": self.network_enabled,
            "humanApproval": self.human_approval,
            "toolPolicy": self.tool_policy.value,
            "timeoutSeconds": self.timeout_seconds,
            "owner": self.owner,
            "workspace": self.workspace,
            "role": self.role,
        }


def normalize_approval_policy(value: object, legacy_mode: object = None) -> ApprovalPolicy:
    """把新旧权限字段归一化为统一审批策略。"""

    normalized = str(value or "").strip().lower()
    if normalized in {item.value for item in ApprovalPolicy}:
        return ApprovalPolicy(normalized)

    legacy = str(legacy_mode or "").strip().lower()
    if legacy == "ask_every_time":
        return ApprovalPolicy.ASK_EVERY_TIME
    if legacy == "bypass":
        return ApprovalPolicy.TRUSTED_TOOLS
    return ApprovalPolicy.CONFIRM_DESTRUCTIVE


def build_permission_profile(source: object | None) -> UnifiedPermissionProfile:
    """从任意 guardrails-like 对象构建统一权限配置。"""

    if not source:
        return UnifiedPermissionProfile()

    policy = normalize_approval_policy(
        getattr(source, "tool_permission_profile", None),
        getattr(source, "tool_permission_mode", None),
    )
    return UnifiedPermissionProfile(
        filesystem_scope=str(getattr(source, "filesystem_scope", "/") or "/"),
        network_enabled=bool(getattr(source, "network_enabled", True)),
        human_approval=bool(getattr(source, "human_approval", False)),
        tool_policy=policy,
        shell_blacklist=tuple(
            str(item).strip()
            for item in list(getattr(source, "shell_blacklist", []) or [])
            if str(item).strip()
        ),
        timeout_seconds=max(1, int(getattr(source, "timeout_seconds", 300) or 300)),
        owner=str(getattr(source, "owner", "local-user") or "local-user"),
        workspace=str(getattr(source, "workspace", "default") or "default"),
        role=str(getattr(source, "role", "owner") or "owner"),
    )


def legacy_mode_from_policy(policy: ApprovalPolicy) -> str:
    """给旧 AgentScope/前端字段保留兼容值。"""

    if policy == ApprovalPolicy.ASK_EVERY_TIME:
        return "ask_every_time"
    if policy in {ApprovalPolicy.TRUSTED_TOOLS, ApprovalPolicy.READ_ONLY}:
        return "bypass"
    return "accept_edits"
