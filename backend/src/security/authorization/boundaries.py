"""平台能力硬边界；与审批模式无关，每次执行前读取当前约束。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.storage.records.permission_records import PermissionRecordStore
from src.tenancy.context import current_tenant
from src.tools.scoped_paths import resolve_workspace_path

from .actions import RuntimeActionBuilder
from .models import ActionSnapshot, OperationScope


@dataclass(frozen=True)
class RuntimeCapabilities:
    """由产品域根据当前 Agent、Project binding 与沙箱状态生成；模型不可提交。"""

    version: str
    filesystem_scope: str
    available_tool_names: frozenset[str]
    denied_tool_names: frozenset[str] = frozenset()
    extra_filesystem_scopes: tuple[str, ...] = ()
    operation_scope: OperationScope = "normal"
    network_enabled: bool = True
    shell_enabled: bool = True
    sandbox_required: bool = False


CapabilityReader = Callable[[], Awaitable[RuntimeCapabilities]]
DomainBoundary = Callable[[ActionSnapshot, Any, str], Awaitable[str | None]]


class RuntimeAuthorizationBoundary:
    """框架无关，组合租户/Run、当前能力、真实工具边界及产品域 Plan 等约束。"""

    def __init__(self, *, builder: RuntimeActionBuilder, capabilities: CapabilityReader,
                 domain_check: DomainBoundary, store: PermissionRecordStore | None = None):
        self.builder = builder
        self.capabilities = capabilities
        self.domain_check = domain_check
        self.store = store or PermissionRecordStore()

    async def __call__(self, action: ActionSnapshot) -> str | None:
        if current_tenant().tenant_id != action.authorization.tenant_id or action.authorization != self.builder.owner:
            return "行动运行归属不匹配"
        error = self.store.run_error(action.authorization)
        if error:
            return error
        try:
            capability = await self.capabilities()
        except (ValueError, OSError):
            return "当前 Agent、Project 或工具能力已失效"
        if capability.version != action.authorization.capability_version:
            return "Agent 能力或 Project 资源版本已变化"
        if action.tool_name in capability.denied_tool_names or action.tool_name not in capability.available_tool_names:
            return "工具被禁止或已不属于当前 Project 能力范围"
        tool = self.builder.tool_for(action)
        if tool is None:
            return "行动工具执行现场不存在"
        module, cls = type(tool).__module__, type(tool).__name__
        is_shell = (module, cls) == ("src.agents.guarded_bash", "GuardedBash")
        is_network = bool(tool.is_mcp) or module in {
            "src.tools.http_request", "src.tools.configured_http_tool",
            "src.tools.knowledge_base_search", "src.tools.web_fetch",
        }
        if capability.operation_scope == "read_only" and (
            action.effect not in {"read_only", "output"} or is_shell or is_network
        ):
            return "Agent 只读能力不允许此操作"
        if is_network and not capability.network_enabled:
            return "Agent 网络能力已关闭"
        if is_shell and not capability.shell_enabled:
            return "Agent 命令执行能力已关闭"
        if is_shell and capability.sandbox_required and tool._backend.__class__.__name__ == "LocalBackend":
            return "命令必须使用隔离沙箱"
        try:
            for target in action.targets:
                if Path(target.target).is_absolute():
                    resolve_workspace_path(target.target, scope_dir=capability.filesystem_scope,
                                           extra_scopes=list(capability.extra_filesystem_scopes))
            raw = self.builder.execution_input(action)
            error = await self.builder.revalidate(action)
            if error:
                return error
            return await self.domain_check(action, tool, raw)
        except (ValueError, OSError):
            return "行动目标、凭据或产品约束已变化"
