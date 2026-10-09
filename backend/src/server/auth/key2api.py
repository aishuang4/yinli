"""把托管用户同步到 key2api，并用该用户的 k2a_ 调用平台模型。

Key 只存在服务器的 auth_provider_keys。接口响应、日志和模型目录都不返回明文。
"""

from __future__ import annotations

from typing import Any

import httpx

from src.config.model_catalog import ModelCatalog, ModelEntry, ProviderConfig
from src.config.runtime_settings import RuntimeModelBinding, resolve_model_binding
from src.config.settings import settings
from src.storage import auth_records

PLATFORM_PREFIX = "platform:"
DEFAULT_BASE_URL = "http://103.236.80.228:3000"


class Key2ApiError(RuntimeError):
    """平台同步或调用失败。消息不得包含 Key。"""

    def __init__(self, message: str, status: int = 502) -> None:
        super().__init__(message)
        self.status = status


def key2api_configured() -> bool:
    """托管且配置了服务令牌时，平台模型走 key2api。"""

    mode = settings.serve_mode.strip().lower()
    return mode == "hosted" and bool(settings.key2api_sync_token.strip())


def base_url() -> str:
    return (settings.key2api_base_url or DEFAULT_BASE_URL).rstrip("/")


def ensure_provider_key(user: dict[str, Any]) -> None:
    """已验证用户还没有可用 Key 时同步一次。失败不抛出，留给下次登录重试。"""

    if not key2api_configured() or user.get("status") != "active":
        return
    existing = auth_records.get_provider_key(str(user["id"]))
    if existing and existing["status"] == "active" and existing["token"]:
        return
    try:
        sync_provider_status(user, "active")
    except Key2ApiError as exc:
        print(f"[auth] 平台 Key 同步失败: {exc}")


def sync_provider_status(user: dict[str, Any], status: str) -> None:
    """按引力用户同步 key2api。未配置服务令牌时不做任何事。"""

    if not key2api_configured():
        return
    payload = _post_sync({
        "external_id": str(user["id"]),
        "email": str(user["email"]),
        "display_name": str(user["email"]),
        "status": status,
    })
    token = str(payload.get("token") or "")
    if not token.startswith("k2a_"):
        raise Key2ApiError("平台没有返回可用的 Key")
    auth_records.save_provider_key(
        user_id=str(user["id"]),
        token=token,
        key2api_user_id=str(payload.get("user_id") or ""),
        key2api_token_id=str(payload.get("token_id") or ""),
        status=status,
    )


def fetch_account_usage(external_id: str) -> dict[str, int] | None:
    """查询这个引力用户自己的额度。未配置服务令牌时返回 None。"""

    if not key2api_configured():
        return None
    response = _request(
        "GET",
        "/api/v1/internal/accounts/usage",
        token=settings.key2api_sync_token.strip(),
        params={"external_id": external_id},
    )
    payload = response.json()
    if not isinstance(payload, dict):
        raise Key2ApiError("平台用量响应无法识别")
    return {
        "tokenQuota": int(payload.get("token_quota") or 0),
        "used": int(payload.get("used") or 0),
        "remaining": int(payload.get("remaining") or 0),
    }


def list_platform_models(user_id: str) -> list[dict[str, Any]]:
    """用该用户的 Key 拉取他能用的模型。响应不含 Key。"""

    record = _active_key(user_id)
    response = _request("GET", "/v1/models", token=record["token"])
    data = response.json().get("data") or []
    models = []
    for item in data:
        model_id = str(item.get("id") or "")
        if not model_id:
            continue
        models.append({
            "id": f"{PLATFORM_PREFIX}{model_id}",
            "label": model_id,
            "providerName": "引力模型",
            "adapter": "openai",
            "capabilities": {},
        })
    return models


def binding_for_platform_model(model_config_id: str, user_id: str) -> RuntimeModelBinding:
    """把平台模型解析成指向 key2api 的 OpenAI 兼容连接。"""

    if not model_config_id.startswith(PLATFORM_PREFIX):
        raise Key2ApiError("不是平台模型", status=422)
    model_name = model_config_id[len(PLATFORM_PREFIX):]
    record = _active_key(user_id)
    provider = ProviderConfig(
        id="key2api",
        name="引力模型",
        adapter="openai",
        baseUrl=f"{base_url()}/v1",
        apiKey=record["token"],
    )
    entry = ModelEntry(
        id=model_config_id,
        providerId="key2api",
        model=model_name,
        label=model_name,
    )
    catalog = ModelCatalog(
        defaultModelId=model_config_id,
        providers=(provider,),
        models=(entry,),
    )
    return resolve_model_binding(model_config_id, catalog=catalog)


async def complete_with_platform_key(model_config_id: str, text: str, user_id: str) -> str:
    """桌面端网关用该用户的 Key 完成一轮补全。"""

    if not model_config_id.startswith(PLATFORM_PREFIX):
        raise Key2ApiError("不是平台模型", status=422)
    record = _active_key(user_id)
    response = _request(
        "POST",
        "/v1/chat/completions",
        token=record["token"],
        json={
            "model": model_config_id[len(PLATFORM_PREFIX):],
            "messages": [{"role": "user", "content": text or ""}],
        },
    )
    payload = response.json()
    choices = payload.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    return str(message.get("content") or "")


def _active_key(user_id: str) -> dict[str, Any]:
    record = auth_records.get_provider_key(user_id)
    if record is None or record["status"] != "active" or not record["token"]:
        raise Key2ApiError("平台模型尚未准备好，请重新登录后再试。", status=409)
    return record


def _post_sync(body: dict[str, str]) -> dict[str, Any]:
    response = _request(
        "POST",
        "/api/v1/internal/accounts/sync",
        token=settings.key2api_sync_token.strip(),
        json=body,
    )
    payload = response.json()
    if not isinstance(payload, dict):
        raise Key2ApiError("平台同步响应无法识别")
    return payload


def _request(
    method: str,
    path: str,
    *,
    token: str,
    json: dict | None = None,
    params: dict[str, str] | None = None,
) -> httpx.Response:
    try:
        response = httpx.request(
            method,
            f"{base_url()}{path}",
            headers={"Authorization": f"Bearer {token}"},
            json=json,
            params=params,
            timeout=30,
        )
    except httpx.HTTPError as exc:
        raise Key2ApiError("平台暂时无法连接") from exc
    if response.status_code == 429:
        raise Key2ApiError("平台额度不足", status=429)
    if response.status_code == 401:
        raise Key2ApiError("平台 Key 已停用或服务令牌无效", status=401)
    if response.status_code >= 400:
        raise Key2ApiError(f"平台请求失败（{response.status_code}）", status=response.status_code)
    return response
