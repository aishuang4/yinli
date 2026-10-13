"""平台模型目录与调用网关。Key 留在服务器，响应不包含供应商密钥。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from src.config.model_catalog import public_catalog
from src.config.runtime_settings import model_binding_scope, resolve_model_binding
from src.server.auth.routes import require_user
PLATFORM_PREFIX = "platform:"
router = APIRouter(prefix="/api/models", tags=["平台模型"])


class CompleteRequest(BaseModel):
    model_config_id: str = Field(alias="modelConfigId")
    text: str = ""

    model_config = {"populate_by_name": True}


def list_provided_models() -> dict[str, Any]:
    """把服务器目录改写成平台 ID，并去掉 Key。"""

    catalog = public_catalog()
    providers = {item["id"]: item for item in catalog.get("providers") or []}
    models = []
    for model in catalog.get("models") or []:
        provider = providers.get(model.get("providerId")) or {}
        models.append({
            "id": f"{PLATFORM_PREFIX}{model['id']}",
            "label": model.get("label") or model.get("model") or model["id"],
            "providerName": provider.get("name") or "",
            "adapter": provider.get("adapter") or "",
            "capabilities": model.get("capabilities") or {},
        })
    return {"models": models}


def record_platform_call(*, user_id: str, model_config_id: str) -> None:
    from src.storage.control_records import record_platform_call as write_audit

    write_audit(user_id=user_id, model_config_id=model_config_id)


def _response_text(response: Any) -> str:
    content = getattr(response, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                parts.append(str(block.get("text") or ""))
            else:
                parts.append(str(getattr(block, "text", "") or ""))
        return "".join(parts)
    return str(response or "")


async def invoke_platform_model(model_config_id: str, text: str) -> str:
    """用服务器 models.json 调用模型。fallback 走同一条降级链。"""

    if not model_config_id.startswith(PLATFORM_PREFIX):
        raise ValueError("不是平台模型")
    from src.server.auth.key2api import key2api_configured
    from src.tenancy.context import current_tenant

    if key2api_configured():
        from src.server.auth.key2api import Key2ApiError, complete_with_platform_key

        owner = current_tenant().user_id
        if not owner:
            raise ValueError("登录后才能使用平台模型")
        try:
            return await complete_with_platform_key(model_config_id, text, owner)
        except Key2ApiError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from None
    local_id = model_config_id[len(PLATFORM_PREFIX):]
    binding = resolve_model_binding(local_id)
    from agentscope.message import UserMsg
    from src.agents.model_factory import create_model_chain

    with model_binding_scope(binding):
        model = create_model_chain(
            [binding.primary.entry.model],
            resolved_models=binding.chain,
        )
        response = await model(messages=[UserMsg(name="user", content=text or "")])
    if hasattr(response, "__aiter__"):
        chunks: list[str] = []
        async for item in response:
            chunks.append(_response_text(item))
        return "".join(chunks)
    return _response_text(response)


@router.get("/provided")
async def provided(request: Request) -> dict[str, Any]:
    from src.config.settings import settings

    if settings.serve_mode.strip().lower() == "desktop":
        return await _desktop_provided_models()
    found = require_user(request)
    from src.server.auth.key2api import Key2ApiError, ensure_provider_key, key2api_configured, list_platform_models

    if key2api_configured():
        try:
            ensure_provider_key(found["user"])
            return {"models": list_platform_models(found["user"]["id"]), "source": "key2api"}
        except Key2ApiError:
            return {"models": [], "source": "key2api"}
    payload = _public_provided()
    payload["source"] = "catalog"
    return payload


def _public_provided() -> dict[str, Any]:
    payload = list_provided_models()
    encoded = json.dumps(payload)
    if "apiKey" in encoded and "apiKeyConfigured" not in encoded:
        raise HTTPException(status_code=500, detail="平台目录泄露了密钥字段")
    return payload


async def _desktop_provided_models() -> dict[str, Any]:
    """桌面端用本机保存的令牌向托管服务取目录，不把 Key 写入 models.json。"""

    import httpx

    from src.server.auth.desktop_account import load_desktop_account

    account = load_desktop_account()
    if account is None:
        return {"models": []}
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(
            f"{account['baseUrl']}/api/models/provided",
            headers={"Authorization": f"Bearer {account['token']}"},
        )
    if response.status_code != 200:
        return {"models": []}
    payload = response.json()
    return {"models": payload.get("models") or [], "source": payload.get("source") or "catalog"}


@router.post("/complete")
async def complete(data: CompleteRequest, request: Request) -> StreamingResponse:
    found = require_user(request)
    text = await invoke_platform_model(data.model_config_id, data.text)
    record_platform_call(user_id=found["user"]["id"], model_config_id=data.model_config_id)

    async def chunks() -> AsyncIterator[str]:
        yield f"data: {json.dumps({'text': text}, ensure_ascii=False)}\n\n"

    return StreamingResponse(chunks(), media_type="text/event-stream")
