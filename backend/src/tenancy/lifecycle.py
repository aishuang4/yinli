"""托管租户的创建、目录和默认数据。可以重复调用。"""

from __future__ import annotations

import logging
from typing import Any

from src.catalog.skill_seed import seed_system_skills
from src.config.runtime_paths import ensure_tenant_directories
from src.storage import control_records, database
from src.tenancy.context import TenantContext, use_tenant

logger = logging.getLogger(__name__)


def ensure_tenant(user: dict[str, Any]) -> TenantContext:
    """保证这个用户有自己的租户目录、库和默认 Project。"""

    user_id = str(user["id"])
    role = str(user.get("role") or "user")
    control_records.insert_tenant(user_id)
    ctx = TenantContext.hosted(user_id, role=role)
    with use_tenant(ctx):
        ensure_tenant_directories()
        database.ensure_db()
        seed_system_skills()
        from src.server.routes.agents import list_registered_agents

        list_registered_agents()
        from src.scheduler.settings import ensure_scheduled_jobs_defaults

        ensure_scheduled_jobs_defaults()
        now = database._utc_now()
        with database._lock, database._connect() as conn:
            database._bind_workspace_resources(
                conn,
                project_id="default",
                workspace_id="default",
                now=now,
            )
        _ensure_empty_model_catalog()
        _sync_provider_key(user)
    return ctx


def _ensure_empty_model_catalog() -> None:
    """还没有 models.json 时写空目录。校验允许空的默认模型。"""

    from src.config.model_catalog import ModelCatalog, _write, models_path

    if models_path().is_file():
        return
    _write(ModelCatalog())


def _sync_provider_key(user: dict[str, Any]) -> None:
    try:
        from src.server.auth.key2api import ensure_provider_key

        ensure_provider_key(user)
    except Exception:
        logger.exception("同步平台 Key 失败 tenant=%s", user.get("id"))
