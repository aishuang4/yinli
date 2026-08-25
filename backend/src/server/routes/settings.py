"""System settings routes."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.agents.factory import clear_all_agents
from src.config.runtime_settings import get_model_settings_public, save_model_settings
from src.scheduler.settings import (
    get_scheduled_jobs_settings_public,
    set_scheduled_jobs_settings,
)
from src.scheduler.ticker import apply_scheduler_enabled

router = APIRouter()


class ModelSettingsUpdate(BaseModel):
    provider: str = Field(..., min_length=1)
    provider_name: str = Field(default="", alias="providerName")
    api_key: str | None = Field(default=None, alias="apiKey")
    clear_api_key: bool = Field(default=False, alias="clearApiKey")
    base_url: str = Field(default="", alias="baseUrl")
    default_model: str = Field(..., alias="defaultModel")
    fallback_models: list[str] = Field(default_factory=list, alias="fallbackModels")
    custom_models: list[str] = Field(default_factory=list, alias="customModels")

    model_config = {"populate_by_name": True}


@router.get("/model")
async def get_model_settings() -> dict:
    """Return model provider settings without exposing the API key."""

    try:
        return get_model_settings_public()
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.put("/model")
async def update_model_settings(data: ModelSettingsUpdate) -> dict:
    """Update runtime model provider settings and clear cached Agents."""

    try:
        result = save_model_settings(data.model_dump(by_alias=True))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    clear_all_agents()
    return result


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
