"""System settings routes."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from src.config.model_catalog import CatalogConflict, public_catalog, save_catalog
from src.config.product_features import load_product_features
from src.group_studio.settings import (
    get_group_studio_settings_public,
    set_group_studio_settings,
)
from src.scheduler.settings import (
    get_scheduled_jobs_settings_public,
    set_scheduled_jobs_settings,
)
from src.scheduler.ticker import apply_scheduler_enabled

router = APIRouter()


def _require_hosted_model_admin(request: Request) -> None:
    """托管普通用户不能写入供应商 Key。桌面本机仍可维护自己的 models.json。"""

    from src.config.settings import settings
    from src.server.auth.routes import token_from_request
    from src.server.auth.service import current_user

    if settings.serve_mode.strip().lower() != "hosted":
        return
    found = current_user(token_from_request(request))
    if found is None or found["user"].get("role") != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


@router.get("/product-features")
async def get_product_features() -> dict:
    """读取打包期产品功能档；运行时只读，不落 SQLite。"""

    return load_product_features().as_public_dict()


@router.get("/models")
async def get_models_catalog() -> dict:
    """读取脱敏模型目录。"""
    try:
        return public_catalog()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.put("/models")
async def update_models_catalog(data: dict, request: Request) -> dict:
    """按版本原子保存目录，运行中 Agent 保留自己的快照。"""

    _require_hosted_model_admin(request)
    payload = dict(data)
    etag = str(payload.pop("etag", ""))
    try:
        return save_catalog(payload, etag)
    except CatalogConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


class ScheduledJobsSettingsUpdate(BaseModel):
    enabled: bool
    capability_gate: bool = Field(default=True, alias="capabilityGate")

    model_config = {"populate_by_name": True}


@router.get("/scheduled-jobs")
async def get_scheduled_jobs_settings() -> dict:
    """读取定时任务调度开关。"""

    return get_scheduled_jobs_settings_public()


@router.put("/scheduled-jobs")
async def update_scheduled_jobs_settings(data: ScheduledJobsSettingsUpdate) -> dict:
    """保存调度开关；打开时启动 ticker，关闭时停止扫描。"""

    set_scheduled_jobs_settings(
        enabled=data.enabled,
        capability_gate=data.capability_gate,
    )
    apply_scheduler_enabled(data.enabled)
    return get_scheduled_jobs_settings_public()


class GroupStudioSettingsUpdate(BaseModel):
    enabled: bool
    max_members: int | None = Field(default=None, alias="maxMembers")
    max_parallel_workers: int | None = Field(default=None, alias="maxParallelWorkers")
    max_turns: int | None = Field(default=None, alias="maxTurns")

    model_config = {"populate_by_name": True}


@router.get("/group-studio")
async def get_group_studio_settings() -> dict:
    """读取群聊工作室开关。flag 关闭时本接口仍可用。"""

    return get_group_studio_settings_public()


@router.put("/group-studio")
async def update_group_studio_settings(data: GroupStudioSettingsUpdate) -> dict:
    """保存工作室开关。关闭后 /api/group-studio 写接口返回 404。"""

    set_group_studio_settings(
        enabled=data.enabled,
        max_members=data.max_members,
        max_parallel_workers=data.max_parallel_workers,
        max_turns=data.max_turns,
    )
    return get_group_studio_settings_public()
