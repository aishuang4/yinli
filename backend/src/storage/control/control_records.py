"""控制面读写：租户状态、配额和平台调用审计。"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from src.config.settings import settings
from src.storage.control import control_db
from src.tenancy.context import TenantContext

TENANT_STATUSES = ("active", "disabled", "deleting", "deleted")


@dataclass(frozen=True)
class TenantQuota:
    """租户配额。缺省字段用部署配置补齐。"""

    disk_mb: int
    upload_mb: int
    max_active_runs: int
    max_jobs: int
    max_firings_per_day: int

    def as_json(self) -> str:
        return json.dumps(
            {
                "disk_mb": self.disk_mb,
                "upload_mb": self.upload_mb,
                "max_active_runs": self.max_active_runs,
                "max_jobs": self.max_jobs,
                "max_firings_per_day": self.max_firings_per_day,
            },
            separators=(",", ":"),
        )


def default_quota() -> TenantQuota:
    return TenantQuota(
        disk_mb=settings.tenant_disk_mb,
        upload_mb=settings.tenant_upload_mb,
        max_active_runs=settings.tenant_max_active_runs,
        max_jobs=settings.tenant_max_jobs,
        max_firings_per_day=settings.tenant_max_firings_per_day,
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _quota_from_json(raw: str | None) -> TenantQuota:
    base = default_quota()
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return base
    if not isinstance(payload, dict):
        return base

    def pick(name: str, fallback: int) -> int:
        value = payload.get(name, fallback)
        if isinstance(value, bool) or not isinstance(value, int):
            return fallback
        return value

    return TenantQuota(
        disk_mb=pick("disk_mb", base.disk_mb),
        upload_mb=pick("upload_mb", base.upload_mb),
        max_active_runs=pick("max_active_runs", base.max_active_runs),
        max_jobs=pick("max_jobs", base.max_jobs),
        max_firings_per_day=pick("max_firings_per_day", base.max_firings_per_day),
    )


def _tenant_row(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "status": row["status"],
        "quota": _quota_from_json(row["quota_json"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "deletedAt": row["deleted_at"],
    }


def list_active_tenants() -> list[TenantContext]:
    """启动和 ticker 要遍历的活跃租户。"""

    control_db.ensure_control_db()
    with control_db._control_lock, control_db.control_connect() as conn:
        rows = conn.execute(
            """
            SELECT tenants.id AS id, COALESCE(auth_users.role, '') AS role
            FROM tenants
            LEFT JOIN auth_users ON auth_users.id = tenants.id
            WHERE tenants.status = 'active'
            ORDER BY tenants.created_at ASC
            """
        ).fetchall()
    return [TenantContext.hosted(row["id"], role=row["role"] or "user") for row in rows]


def get_tenant(tenant_id: str) -> dict[str, Any] | None:
    control_db.ensure_control_db()
    with control_db._control_lock, control_db.control_connect() as conn:
        row = conn.execute("SELECT * FROM tenants WHERE id = ?", (tenant_id,)).fetchone()
    return _tenant_row(row) if row else None


def insert_tenant(tenant_id: str) -> dict[str, Any]:
    """幂等创建。已存在的租户保持原状态和配额。"""

    control_db.ensure_control_db()
    now = _now()
    with control_db._control_lock, control_db.control_connect() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO tenants (
                id, status, quota_json, created_at, updated_at
            ) VALUES (?, 'active', ?, ?, ?)
            """,
            (tenant_id, default_quota().as_json(), now, now),
        )
        row = conn.execute("SELECT * FROM tenants WHERE id = ?", (tenant_id,)).fetchone()
    return _tenant_row(row)


def set_tenant_status(tenant_id: str, status: str) -> dict[str, Any]:
    if status not in TENANT_STATUSES:
        raise ValueError(f"非法租户状态: {status}")
    control_db.ensure_control_db()
    now = _now()
    deleted_at = now if status == "deleted" else None
    with control_db._control_lock, control_db.control_connect() as conn:
        updated = conn.execute(
            """
            UPDATE tenants
            SET status = ?, updated_at = ?, deleted_at = CASE WHEN ? = 'deleted' THEN ? ELSE deleted_at END
            WHERE id = ?
            """,
            (status, now, status, deleted_at, tenant_id),
        )
        if updated.rowcount == 0:
            raise LookupError(f"租户不存在: {tenant_id}")
        row = conn.execute("SELECT * FROM tenants WHERE id = ?", (tenant_id,)).fetchone()
    return _tenant_row(row)


def get_tenant_quota(tenant_id: str) -> TenantQuota:
    tenant = get_tenant(tenant_id)
    if tenant is None:
        return default_quota()
    return tenant["quota"]


def record_platform_call(
    *,
    user_id: str,
    model_config_id: str,
    client_kind: str = "",
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    status: int = 200,
) -> None:
    """写入平台调用审计。调用方负责传入已发生的用量。"""

    control_db.ensure_control_db()
    with control_db._control_lock, control_db.control_connect() as conn:
        conn.execute(
            """
            INSERT INTO platform_call_audit (
                id, user_id, model_config_id, client_kind,
                prompt_tokens, completion_tokens, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"pca_{uuid.uuid4().hex}",
                user_id,
                model_config_id,
                client_kind,
                prompt_tokens,
                completion_tokens,
                status,
                _now(),
            ),
        )
