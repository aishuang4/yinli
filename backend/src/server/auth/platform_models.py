"""平台模型目录。Key 留在服务器，响应不包含供应商密钥。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from src.server.auth.routes import require_user

router = APIRouter(prefix="/api/models", tags=["平台模型"])


@router.get("/provided")
async def provided(request: Request) -> dict[str, Any]:
    from src.config.settings import settings

    if settings.serve_mode.strip().lower() == "desktop":
        return await _desktop_provided_models()
    require_user(request)
    from src.models.platform_layer import list_platform_models
    from src.server.auth.key2api import Key2ApiError, ensure_provider_key, key2api_configured

    if not key2api_configured():
        raise HTTPException(status_code=503, detail="平台模型暂不可用，请稍后重试或添加自己的模型")
    try:
        ensure_provider_key(require_user(request)["user"])
        return {"models": list_platform_models(), "source": "key2api"}
    except Key2ApiError:
        return {"models": [], "source": "key2api"}


async def _desktop_provided_models() -> dict[str, Any]:
    """桌面端向云端转发接口取目录，不把平台 Key 写入 models.json。"""

    from src.server.auth.cloud_client import list_models

    return {"models": await list_models(), "source": "cloud"}
