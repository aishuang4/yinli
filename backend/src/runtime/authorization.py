"""产品域接入平台授权的公开合同；不暴露 SDK 或 Chat 私有对象。"""

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
    "build_run_authorization", "public_authorization_event",
]
