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


@router.get("/models/permission-review/status")
async def get_permission_review_status() -> dict:
    """报告评测门禁状态；验收事实不由模型设置页面自行写入。"""
    from src.security.authorization.review_gate import permission_review_status

    return permission_review_status()


@router.get("/product-features")
async def get_product_features() -> dict:
    """读取打包期产品功能档；运行时只读，不落 SQLite。"""

    return load_product_features().as_public_dict()


@router.get("/models")
async def get_models_catalog() -> dict:
    """读取脱敏模型目录。托管下先把上游模型名写入当前租户目录。"""

    from src.config.model_catalog import ensure_hosted_upstream_catalog

    try:
        ensure_hosted_upstream_catalog()
        return public_catalog()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


class UpstreamModelsRequest(BaseModel):
    provider_id: str = Field(alias="providerId")

    model_config = {"populate_by_name": True}


@router.post("/models/upstream")
async def list_upstream_models(data: UpstreamModelsRequest) -> dict:
    """列出某个已保存供应商的上游模型名。Key 留在服务端。"""

    from src.config.model_catalog import CatalogError, list_provider_upstream_models

    try:
        names = list_provider_upstream_models(data.provider_id)
    except CatalogError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return {"models": names}


@router.put("/models")
async def update_models_catalog(data: dict, request: Request) -> dict:
    """按版本原子保存当前租户的目录，运行中 Agent 保留自己的快照。"""

    del request
    payload = dict(data)
    etag = str(payload.pop("etag", ""))
    _reject_private_model_urls(payload)
    try:
        return save_catalog(payload, etag)
    except CatalogConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


def _reject_private_model_urls(payload: dict) -> None:
    """托管下自定义模型地址必须是公网 HTTP(S)。"""

    from src.security.egress import EgressBlocked, assert_public_url_sync
    from src.tenancy.context import is_hosted_mode

    if not is_hosted_mode():
        return
    providers = payload.get("providers") or []
    if not isinstance(providers, list):
        return
    for provider in providers:
        if not isinstance(provider, dict):
            continue
        url = str(provider.get("baseUrl") or provider.get("base_url") or "").strip()
        if not url:
            continue
        try:
            assert_public_url_sync(url, purpose="model")
        except EgressBlocked as exc:
            raise HTTPException(status_code=422, detail="模型地址不能指向内网或本机") from exc


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
