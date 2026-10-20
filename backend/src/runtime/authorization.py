"""产品域接入平台授权的公开合同；不暴露 SDK 或 Chat 私有对象。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import hashlib
import json
from typing import Any, Protocol

from src.security.authorization.models import (
    ActionSnapshot,
    ApprovalMode,
    ApprovalRecord,
    AuthorizationDecision,
    ExecutionRecord,
    RunAuthorization,
    TargetVersion,
)
from src.security.authorization.service import PlatformAuthorizationService


class AuthorizationService(Protocol):
    """决策与执行前复核接口，存储和运行现场由实现负责。"""

    async def authorize(self, action: ActionSnapshot) -> AuthorizationDecision:
        """为精确行动获取唯一决策。"""
        ...

    async def reserve_execution(self, action: ActionSnapshot, decision_id: str) -> ExecutionRecord:
        """复核当前边界与目标后原子消费许可，失败不能启动执行。"""
        ...

    async def start_execution(self, action: ActionSnapshot, decision_id: str) -> ExecutionRecord:
        """实际调用前再复核许可，从 reserved 原子进入 running。"""
        ...

    async def finish_execution(self, action: ActionSnapshot, decision_id: str, *, status: str) -> bool:
        """收口执行事实；unknown 与其他终态都不能重新执行。"""
        ...


AUTHORIZATION_ADAPTER_VERSION = "2"


async def build_run_authorization(*, runtime_context, capability_actor_id: str, actor_id: str,
                                  run_id: str, session_id: str, tool_scope,
                                  denied_tool_names=frozenset(), restriction=None, plan_mode_manager=None):
    """attached child 继承原 Run 的审核模式，能力只允许在父 Agent 上继续收窄。"""

    from types import SimpleNamespace
    from src.server.routes.chat.authorization import build_chat_authorization

    ctx = SimpleNamespace(runtime_context=runtime_context, agent_id=capability_actor_id,
                          session_id=session_id, runtime_tool_scope=tool_scope,
                          tool_name_denylist=set(denied_tool_names), extra_filesystem_scopes=[])
    return await build_chat_authorization(ctx, actor_id=actor_id, run_id=run_id, restriction=restriction, plan_mode_manager=plan_mode_manager)


def inherit_child_authorization(
    parent: RunAuthorization,
    *,
    actor_id: str,
    parent_denied: frozenset[str] = frozenset(),
    child_denied: frozenset[str] = frozenset(),
    parent_filesystem_scope: str = "",
    child_filesystem_scope: str = "",
) -> tuple[RunAuthorization, frozenset[str]]:
    """子執行者繼承父 Run 的模式與 Run，能力只允許再收窄。"""

    from pathlib import Path

    if not actor_id.strip():
        raise ValueError("子执行者缺少 actor")
    if parent_filesystem_scope and child_filesystem_scope:
        parent_path = Path(parent_filesystem_scope).expanduser().resolve()
        child_path = Path(child_filesystem_scope).expanduser().resolve()
        if not child_path.is_relative_to(parent_path):
            raise ValueError("子执行者不能超出父文件系统范围")
    denied = frozenset(parent_denied) | frozenset(child_denied)
    if parent_denied - denied:
        raise ValueError("子执行者不能恢复父级已禁止的工具")
    child = parent.model_copy(update={"actor_id": actor_id})
    return child, denied


def open_run_binding(
    owner: RunAuthorization,
    *,
    filesystem_scope: str,
    denied_tool_names: frozenset[str] = frozenset(),
    tool_names: Callable[[], Awaitable[frozenset[str]]] | None = None,
    extra_filesystem_scopes: tuple[str, ...] = (),
    parent_capabilities: Callable[[], Awaitable[Any]] | None = None,
    share_denials_from: Any | None = None,
    domain_check: Callable[..., Awaitable[str | None]] | None = None,
) -> RunAuthorizationBinding:
    """同一執行鏈供 Leader、Member 和 Subagent 使用；子邊界只會更窄。"""

    from src.security.authorization.actions import RuntimeActionBuilder
    from src.security.authorization.boundaries import RuntimeAuthorizationBoundary, RuntimeCapabilities
    from src.security.authorization.service import PlatformAuthorizationService

    builder = RuntimeActionBuilder(
        owner,
        filesystem_scope=filesystem_scope,
        extra_filesystem_scopes=extra_filesystem_scopes,
    )

    async def capabilities() -> RuntimeCapabilities:
        names = await tool_names() if tool_names is not None else frozenset()
        parent = await parent_capabilities() if parent_capabilities is not None else None
        version = parent.version if parent is not None else owner.capability_version
        return RuntimeCapabilities(
            version=version,
            filesystem_scope=(parent.filesystem_scope if parent is not None else filesystem_scope) or "/",
            available_tool_names=names,
            denied_tool_names=denied_tool_names | (parent.denied_tool_names if parent is not None else frozenset()),
            extra_filesystem_scopes=parent.extra_filesystem_scopes if parent is not None else extra_filesystem_scopes,
            operation_scope=parent.operation_scope if parent is not None else "normal",
            network_enabled=parent.network_enabled if parent is not None else True,
            shell_enabled=parent.shell_enabled if parent is not None else True,
            sandbox_required=parent.sandbox_required if parent is not None else False,
        )

    async def allow_domain(action, tool, raw):
        return None

    boundary = RuntimeAuthorizationBoundary(
        builder=builder,
        capabilities=capabilities,
        domain_check=domain_check or allow_domain,
    )
    service = PlatformAuthorizationService(boundary_check=boundary, revalidate=builder.revalidate)
    if share_denials_from is not None:
        service._actor_denials = share_denials_from._actor_denials
        service._run_denials = share_denials_from._run_denials
        service._boundary_denials = share_denials_from._boundary_denials
    return RunAuthorizationBinding(owner=owner, service=service, action_builder=builder)


def public_authorization_event(agent, event_type: str, data: dict) -> dict:
    """跨域流只投递脱敏后的权限与执行事实。"""

    from src.server.routes.chat.authorization_display import public_authorization_event as display_event

    return display_event(agent, event_type, data)


@dataclass(frozen=True)
class RunAuthorizationBinding:
    """产品域向唯一 Agent 工厂提交的本轮授权现场。"""

    owner: RunAuthorization
    service: AuthorizationService
    action_builder: Callable[[Any, str, str], Awaitable[ActionSnapshot]]

    def cache_key(self) -> str:
        """运行 ID 每轮刷新；会话、actor、模式、能力和 adapter 版本隔离实例。"""

        values = self.owner.model_dump(mode="json", by_alias=True)
        values.pop("runId")
        values["adapterVersion"] = AUTHORIZATION_ADAPTER_VERSION
        return hashlib.sha256(json.dumps(values, sort_keys=True).encode("utf-8")).hexdigest()


__all__ = [
    "ActionSnapshot", "ApprovalMode", "ApprovalRecord", "AuthorizationDecision",
    "AuthorizationService", "ExecutionRecord", "PlatformAuthorizationService",
    "RunAuthorization", "TargetVersion", "RunAuthorizationBinding", "AUTHORIZATION_ADAPTER_VERSION",
    "build_run_authorization", "inherit_child_authorization", "open_run_binding",
    "public_authorization_event",
]
