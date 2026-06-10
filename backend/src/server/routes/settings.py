"""System settings routes."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.agents.factory import clear_all_agents
from src.config.runtime_settings import get_model_settings_public, save_model_settings

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

    return get_model_settings_public()


@router.put("/model")
async def update_model_settings(data: ModelSettingsUpdate) -> dict:
    """Update runtime model provider settings and clear cached Agents."""

    try:
        result = save_model_settings(data.model_dump(by_alias=True))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    clear_all_agents()
    return result
