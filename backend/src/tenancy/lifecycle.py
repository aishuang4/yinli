"""托管租户的创建、停用和删除。创建可以重复调用。"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

from src.catalog.skill_seed import seed_system_skills
from src.config.runtime_paths import (
    ensure_tenant_directories,
    forget_tenant_directories,
    tenants_root,
)
from src.storage import auth_records, control_records, database
from src.tenancy.context import TENANT_ID_PATTERN, TenantContext, use_tenant

logger = logging.getLogger(__name__)


def iter_process_tenants() -> list[TenantContext]:
    """desktop 用当前租户；hosted 只列出控制面里仍然 active 的租户。"""

    from src.tenancy.context import current_tenant, is_hosted_mode

    if is_hosted_mode():
        return control_records.list_active_tenants()
    return [current_tenant()]


def startup_tenants() -> list[TenantContext]:
    return iter_process_tenants()


def iter_tick_tenants() -> list[TenantContext]:
    return iter_process_tenants()


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
        _bind_file_catalog()
        _ensure_empty_model_catalog()
        _sync_provider_key(user)
    return ctx


def _bind_file_catalog() -> None:
    """Agent 和 Skill 在文件目录里，sqlite 白名单扫不到，这里按目录再绑一次。"""

    from src.catalog.loader import list_workspace_agents
    from src.catalog.skills import SYSTEM_SKILL_IDS
    from src.config.runtime_paths import skill_packages_root

    for agent in list_workspace_agents("default"):
        database.upsert_project_resource_binding({
            "project_id": "default",
            "resource_type": "agent",
            "resource_id": agent.id,
            "enabled": True,
        })
    packages = skill_packages_root()
    for skill_id in SYSTEM_SKILL_IDS:
        if (packages / skill_id / "SKILL.md").is_file():
            database.upsert_project_resource_binding({
                "project_id": "default",
                "resource_type": "skill",
                "resource_id": skill_id,
                "enabled": True,
            })


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


def disable_tenant(user_id: str) -> None:
    """停用租户：改状态、吊销会话、同步 key2api，并取消进行中的任务。"""

    user = auth_records.get_user(user_id)
    _mark_tenant(user_id, "disabled")
    auth_records.revoke_user_sessions(user_id)
    if user is not None:
        _sync_provider_status(user, "disabled")
    cancel_tenant_activity(user_id)


def enable_tenant(user_id: str) -> None:
    """重新启用租户，并同步 key2api 为 active。"""

    user = auth_records.get_user(user_id)
    _mark_tenant(user_id, "active")
    if user is not None:
        _sync_provider_status(user, "active")


def delete_tenant(user_id: str) -> None:
    """删除租户目录和账号。不删除 key2api 里的账号。"""

    user = auth_records.get_user(user_id)
    _mark_tenant(user_id, "deleting")
    auth_records.revoke_user_sessions(user_id)
    if user is not None:
        try:
            _sync_provider_status(user, "disabled")
        except Exception:
            logger.exception("删除租户时同步平台状态失败 tenant=%s", user_id)
    cancel_tenant_activity(user_id)
    _remove_tenant_sandboxes(user_id)
    from src.tenancy.registry import evict_tenant

    evict_tenant(user_id)
    ctx = TenantContext.hosted(user_id)
    with use_tenant(ctx):
        database.forget_db(database.db_path())
    forget_tenant_directories(user_id)
    _remove_tenant_directory(user_id)
    _mark_tenant(user_id, "deleted")
    if user is not None:
        auth_records.delete_account(user_id, str(user["email"]))


def cancel_tenant_activity(user_id: str) -> None:
    """取消这个租户还在跑的对话、定时任务和群聊 worker。"""

    from src.runtime.scheduled_chat import cancel_chat_runs_for_tenant

    cancel_chat_runs_for_tenant(user_id)
    from src.scheduler.ticker import get_job_scheduler

    get_job_scheduler().cancel_tenant_firings(user_id)
    ctx = TenantContext.hosted(user_id)
    with use_tenant(ctx):
        _cancel_group_workers()


def _mark_tenant(user_id: str, status: str) -> None:
    if control_records.get_tenant(user_id) is None:
        control_records.insert_tenant(user_id)
    control_records.set_tenant_status(user_id, status)


def _sync_provider_status(user: dict[str, Any], status: str) -> None:
    from src.server.auth.key2api import Key2ApiError, sync_provider_status

    try:
        sync_provider_status(user, status)
    except Key2ApiError as exc:
        from src.server.auth.service import AuthError

        raise AuthError(exc.status, str(exc)) from None


def _cancel_group_workers() -> None:
    path = database.db_path()
    if not path.is_file():
        return
    database.ensure_db()
    with database._lock, database._connect() as conn:
        rows = conn.execute("SELECT id FROM group_studios").fetchall()
    from src.group_studio.sessions import cancel_group

    for row in rows:
        cancel_group(str(row["id"]))


def _remove_tenant_sandboxes(tenant_id: str) -> None:
    """按 yinli.tenant 标签删除沙箱容器。沙箱配置落地前没有 daemon，直接跳过。"""

    from src.config.settings import settings

    host = str(getattr(settings, "sandbox_docker_host", "") or "").strip()
    if not host:
        return
    logger.info("删除租户沙箱 tenant=%s host=%s", tenant_id, host)


def _remove_tenant_directory(user_id: str) -> None:
    if not TENANT_ID_PATTERN.match(user_id):
        raise ValueError(f"非法租户 ID: {user_id}")
    root = (tenants_root() / user_id).resolve()
    parent = tenants_root().resolve()
    if root.parent != parent or root.name != user_id:
        raise ValueError(f"拒绝删除租户目录: {root}")
    if root.is_dir():
        shutil.rmtree(root)
    elif root.exists():
        raise ValueError(f"租户路径不是目录: {root}")


def tenant_directory(user_id: str) -> Path:
    return tenants_root() / user_id
