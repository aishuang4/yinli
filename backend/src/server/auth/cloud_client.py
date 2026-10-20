"""桌面端访问引力云端的账号与平台模型。

令牌放在本机 secret_store，不写入 hosted-account.json。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

import httpx

from src.config.model_catalog import ModelCatalog, ModelEntry, ProviderConfig
from src.config.runtime_settings import RuntimeModelBinding, resolve_model_binding
from src.config.settings import settings

CLOUD_SECRET = "secret:cloud-session-token"
_CACHE_SECONDS = 300
_cached_at = 0.0
_cached_models: list[dict] = []


class CloudSessionError(RuntimeError):
    """云端登录不可用。消息可以展示给用户。"""

    def __init__(self, message: str, *, status: int = 401) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class CloudAccount:
    base_url: str
    email: str
    status: str
    token: str


def account_file():
    from src.server.auth.desktop_account import account_path

    return account_path()


def load_account() -> CloudAccount | None:
    """读取云端账号。旧文件里的明文令牌会迁入密钥存储。"""

    path = account_file()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    base_url = str(data.get("baseUrl") or "").rstrip("/")
    email = str(data.get("email") or "")
    status = str(data.get("status") or "active")
    if not base_url:
        return None
    legacy = str(data.get("token") or "")
    if legacy:
        from src.config.secret_store import set_named_secret

        set_named_secret(CLOUD_SECRET, legacy)
        _write_public_file(base_url=base_url, email=email, status=status)
        token = legacy
    else:
        from src.config.secret_store import get_named_secret

        token = get_named_secret(CLOUD_SECRET)
    return CloudAccount(base_url=base_url, email=email, status=status, token=token)


def save_account(base_url: str, email: str, token: str) -> None:
    from src.config.secret_store import set_named_secret

    set_named_secret(CLOUD_SECRET, token)
    _write_public_file(base_url=base_url.rstrip("/"), email=email, status="active")
    clear_model_cache()


def clear_account() -> None:
    from src.config.secret_store import delete_named_secret

    delete_named_secret(CLOUD_SECRET)
    path = account_file()
    if path.is_file():
        path.unlink()
    clear_model_cache()


def mark_expired() -> None:
    """保留邮箱和地址，让界面提示重新登录。"""

    account = load_account()
    if account is None:
        return
    _write_public_file(base_url=account.base_url, email=account.email, status="expired")
    clear_model_cache()


def clear_model_cache() -> None:
    global _cached_at, _cached_models
    _cached_at = 0.0
    _cached_models = []


def public_account() -> dict:
    account = load_account()
    if account is None:
        return {"connected": False, "email": "", "baseUrl": "", "status": ""}
    return {
        "connected": account.status == "active" and bool(account.token),
        "email": account.email,
        "baseUrl": account.base_url,
        "status": account.status,
    }


async def list_models() -> list[dict]:
    return list_models_sync()


def list_models_sync() -> list[dict]:
    """拉取云端平台模型。401 时把本机状态标为 expired 并返回空列表。"""

    global _cached_at, _cached_models
    now = time.monotonic()
    if _cached_models and now - _cached_at < _CACHE_SECONDS:
        return list(_cached_models)
    account = load_account()
    if account is None or account.status != "active" or not account.token:
        return []
    try:
        response = httpx.get(
            f"{account.base_url}/api/platform/v1/models",
            headers={"Authorization": f"Bearer {account.token}"},
            timeout=20,
        )
    except httpx.HTTPError:
        return []
    if response.status_code == 401:
        mark_expired()
        return []
    if response.status_code != 200:
        return []
    payload = response.json()
    models = []
    for item in payload.get("data") or []:
        model_id = str(item.get("id") or "")
        if not model_id:
            continue
        models.append({
            "id": model_id,
            "label": model_id.removeprefix("platform:"),
            "providerName": "引力云端",
            "adapter": "openai",
            "capabilities": item.get("capabilities") or {},
        })
    _cached_at = now
    _cached_models = models
    return list(models)


def binding(model_config_id: str) -> RuntimeModelBinding:
    """桌面平台模型走云端 OpenAI 兼容转发，Key 是登录令牌而不是平台 Key。"""

    account = load_account()
    if account is None or not account.token:
        raise CloudSessionError("登录引力云端后才能使用平台模型")
    if account.status == "expired":
        raise CloudSessionError("云端登录已失效，请重新登录")
    listed = {item["id"]: item for item in list_models_sync()}
    found = listed.get(model_config_id) or {}
    capabilities = found.get("capabilities") or {}
    provider = ProviderConfig(
        id="yinli-cloud",
        name="引力云端",
        adapter="openai",
        baseUrl=f"{account.base_url}/api/platform/v1",
        apiKey=account.token,
    )
    entry = ModelEntry(
        id=model_config_id,
        providerId="yinli-cloud",
        model=model_config_id,
        label=str(found.get("label") or model_config_id.removeprefix("platform:")),
        capabilities={"images": bool(capabilities.get("images"))},
    )
    catalog = ModelCatalog(
        defaultModelId=model_config_id,
        providers=(provider,),
        models=(entry,),
    )
    resolved = resolve_model_binding(model_config_id, catalog=catalog)
    if len(resolved.chain) != 1:
        raise CloudSessionError("平台模型暂不可用，请稍后重试或添加自己的模型", status=502)
    return resolved


async def login_cloud(*, base_url: str, email: str, password: str) -> dict:
    url = (base_url or settings.cloud_base_url).strip().rstrip("/")
    if not url:
        raise CloudSessionError("安装包未配置云端地址", status=422)
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                f"{url}/api/auth/login",
                json={"email": email, "password": password, "clientKind": "desktop"},
            )
    except httpx.HTTPError as exc:
        raise CloudSessionError("托管登录失败", status=502) from exc
    if response.status_code != 200:
        raise CloudSessionError("托管登录失败", status=response.status_code)
    body = response.json()
    token = str(body.get("token") or "")
    if not token:
        raise CloudSessionError("托管服务没有返回桌面令牌", status=502)
    saved_email = str((body.get("user") or {}).get("email") or email)
    save_account(url, saved_email, token)
    return public_account()


async def logout_cloud() -> None:
    account = load_account()
    if account is not None and account.token and account.base_url:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(
                    f"{account.base_url}/api/auth/logout",
                    headers={"Authorization": f"Bearer {account.token}"},
                )
        except httpx.HTTPError:
            pass
    clear_account()


def _write_public_file(*, base_url: str, email: str, status: str) -> None:
    path = account_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"baseUrl": base_url, "email": email, "status": status},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    try:
        path.chmod(0o600)
    except OSError:
        pass
