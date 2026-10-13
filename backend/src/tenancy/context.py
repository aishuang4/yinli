"""租户上下文：谁在访问数据、数据根在哪里。

只依赖 ``config.runtime_paths`` 与 ``config.settings``，不反向 import 业务模块。
"""

from __future__ import annotations

import asyncio
import re
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from pathlib import Path
from typing import Coroutine, Iterator, Literal

from src.config import runtime_paths
from src.config.settings import settings

TENANT_ID_PATTERN = re.compile(r"^usr_[0-9a-f]{32}$")
LOCAL_TENANT_ID = "local"


class TenantMissing(RuntimeError):
    """hosted 下没有租户上下文时访问租户数据。"""


@dataclass(frozen=True)
class TenantContext:
    tenant_id: str
    data_root: Path
    kind: Literal["local", "hosted"]
    user_id: str = ""  # hosted 时等于 tenant_id；local 时为空
    user_role: str = ""  # hosted 时为 auth_users.role

    @property
    def is_hosted(self) -> bool:
        return self.kind == "hosted"

    @staticmethod
    def local() -> "TenantContext":
        return TenantContext(
            tenant_id=LOCAL_TENANT_ID,
            data_root=runtime_paths.USER_DATA_ROOT,
            kind="local",
        )

    @staticmethod
    def hosted(user_id: str, *, role: str = "user") -> "TenantContext":
        if not TENANT_ID_PATTERN.match(user_id):
            raise ValueError(f"非法租户 ID: {user_id!r}")
        return TenantContext(
            tenant_id=user_id,
            data_root=runtime_paths.tenants_root() / user_id,
            kind="hosted",
            user_id=user_id,
            user_role=role,
        )


_TENANT: ContextVar[TenantContext | None] = ContextVar("yinli_tenant", default=None)
_local_cache: TenantContext | None = None


def serve_mode() -> str:
    return settings.serve_mode.strip().lower() or "desktop"


def is_hosted_mode() -> bool:
    return serve_mode() == "hosted"


def _local_singleton() -> TenantContext:
    # USER_DATA_ROOT 可能被测试替换，路径变化时重建缓存。
    global _local_cache
    root = runtime_paths.USER_DATA_ROOT
    if _local_cache is None or _local_cache.data_root != root:
        _local_cache = TenantContext(
            tenant_id=LOCAL_TENANT_ID,
            data_root=root,
            kind="local",
        )
    return _local_cache


def current_tenant() -> TenantContext:
    ctx = _TENANT.get()
    if ctx is not None:
        return ctx
    if is_hosted_mode():
        raise TenantMissing("托管模式下缺少租户上下文")
    return _local_singleton()


def current_tenant_or_none() -> TenantContext | None:
    """只给中间件和日志用；业务代码应使用 current_tenant()。"""

    return _TENANT.get()


def set_tenant(ctx: TenantContext) -> Token:
    return _TENANT.set(ctx)


def reset_tenant(token: Token) -> None:
    _TENANT.reset(token)


@contextmanager
def use_tenant(ctx: TenantContext) -> Iterator[TenantContext]:
    token = _TENANT.set(ctx)
    try:
        yield ctx
    finally:
        _TENANT.reset(token)


def spawn_in_tenant(ctx: TenantContext, coro: Coroutine, *, name: str | None = None) -> asyncio.Task:
    """在 ctx 下创建任务。用于不从请求派生的后台任务。"""

    with use_tenant(ctx):
        return asyncio.create_task(coro, name=name)
