"""统一资源权限模型。

Agent 档案只提供能力边界。工具放行由平台授权引擎决定，不读取旧审批档。
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
    """运行期能力配置。审批不在这里决定。"""

    filesystem_scope: str = "/"
    network_enabled: bool = True
    tool_policy: ApprovalPolicy = ApprovalPolicy.TRUSTED_TOOLS
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

    @property
    def can_execute(self) -> bool:
        """是否允许本地命令执行；该能力不依赖网络开关。"""

        return not self.is_read_only

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

        return PermissionDecision.ALLOW

    def to_agentscope_permission_mode(self) -> PermissionMode:
        """Agent 档案不再选择审批模式。平台授权引擎决定放行。"""

        return PermissionMode.BYPASS

    def cache_key(self) -> str:
        """生成影响 Agent 运行时缓存的稳定 key。"""

        return (
            f"scope-{self.filesystem_scope}:net-{self.network_enabled}:"
            f"policy-{self.tool_policy.value}:"
            f"timeout-{self.timeout_seconds}:blacklist-{','.join(self.shell_blacklist)}"
        )

    def audit_payload(self) -> dict[str, Any]:
        """生成可写入运行轨迹或审计记录的安全摘要。"""

        return {
            "filesystemScope": self.filesystem_scope,
            "networkEnabled": self.network_enabled,
            "toolPolicy": self.tool_policy.value,
            "timeoutSeconds": self.timeout_seconds,
            "owner": self.owner,
            "workspace": self.workspace,
            "role": self.role,
        }


def build_permission_profile(source: object | None) -> UnifiedPermissionProfile:
    """从 guardrails 构建能力配置。旧审批档不参与，只读能力仍然收窄工具。"""

    if not source:
        return UnifiedPermissionProfile()

    operation_scope = str(getattr(source, "operation_scope", "") or "").strip().lower()
    legacy_profile = str(getattr(source, "tool_permission_profile", "") or "").strip().lower()
    read_only = operation_scope == "read_only" or legacy_profile == "read_only"
    return UnifiedPermissionProfile(
        filesystem_scope=str(getattr(source, "filesystem_scope", "/") or "/"),
        network_enabled=bool(getattr(source, "network_enabled", True)),
        tool_policy=ApprovalPolicy.READ_ONLY if read_only else ApprovalPolicy.TRUSTED_TOOLS,
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
