"""租户上下文与按租户分桶的进程内状态。

hosted 下每个用户一份独立数据根，代码路径取不到租户时必须抛错（fail closed）；
desktop 是固定的 ``local`` 租户，不设置上下文时自动返回。
"""

from src.tenancy.context import (
    LOCAL_TENANT_ID,
    TENANT_ID_PATTERN,
    TenantContext,
    TenantMissing,
    current_tenant,
    current_tenant_or_none,
    is_hosted_mode,
    reset_tenant,
    serve_mode,
    set_tenant,
    spawn_in_tenant,
    use_tenant,
)

__all__ = [
    "LOCAL_TENANT_ID",
    "TENANT_ID_PATTERN",
    "TenantContext",
    "TenantMissing",
    "current_tenant",
    "current_tenant_or_none",
    "is_hosted_mode",
    "reset_tenant",
    "serve_mode",
    "set_tenant",
    "spawn_in_tenant",
    "use_tenant",
]
