"""平台模型层。

hosted 用当前租户用户的 key2api Key。desktop 经云端转发，cloud_client 在任务 5.6 接入。
能力表在进程级 shared/platform-models.json，没有记录时用默认值。
"""

from __future__ import annotations

import json
import os
from typing import Any

from src.config.model_catalog import Capabilities, CatalogError
from src.config.runtime_paths import shared_root
from src.config.runtime_settings import ResolvedModel, RuntimeModelBinding

PLATFORM_PREFIX = "platform:"
_DEFAULT_CAPABILITIES = {"tools": True, "images": False, "thinking": False}
_CAPABILITY_KEYS = tuple(_DEFAULT_CAPABILITIES)
_NO_PLATFORM_KEY = "平台模型暂不可用，请稍后重试或添加自己的模型"
_LOCAL_LOGIN = "登录引力云端后才能使用平台模型"


def list_platform_models() -> list[dict[str, Any]]:
    """当前进程能选的平台模型。未登录的桌面端返回空列表。"""

    from src.tenancy.context import is_hosted_mode

    if is_hosted_mode():
        return _hosted_models()
    return _local_models()


def platform_binding(model_config_id: str) -> RuntimeModelBinding:
    """解析一个 platform: 模型。降级链只有这一项。"""

    from src.tenancy.context import is_hosted_mode

    if is_hosted_mode():
        return _hosted_binding(model_config_id)
    return _local_binding(model_config_id)


def platform_capabilities(model_name: str) -> dict[str, bool]:
    """能力表里的记录；没有记录时 tools 开、images 和 thinking 关。"""

    record = _capability_table().get(model_name)
    if not isinstance(record, dict):
        return dict(_DEFAULT_CAPABILITIES)
    raw = record.get("capabilities")
    if not isinstance(raw, dict):
        return dict(_DEFAULT_CAPABILITIES)
    merged = dict(_DEFAULT_CAPABILITIES)
    for key in _CAPABILITY_KEYS:
        if isinstance(raw.get(key), bool):
            merged[key] = raw[key]
    return merged


def read_platform_capability_document() -> dict[str, Any]:
    """管理员读取的能力表。文件不存在时返回空表。"""

    path = shared_root() / "platform-models.json"
    if not path.is_file():
        return {"version": 1, "models": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {"version": 1, "models": []}
    if not isinstance(payload, dict):
        return {"version": 1, "models": []}
    return payload


def save_platform_capability_document(payload: dict[str, Any]) -> dict[str, Any]:
    """原子替换能力表。字段与设计中的 tools / images / thinking 一致。"""

    document = _normalize_capability_document(payload)
    path = shared_root() / "platform-models.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    return document


def _normalize_capability_document(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
        raise ValueError("平台模型能力表格式无效")
    models = []
    seen: set[str] = set()
    for item in payload["models"]:
        if not isinstance(item, dict):
            raise ValueError("平台模型能力表格式无效")
        name = str(item.get("model") or "").strip()
        label = str(item.get("label") or "").strip()
        if not name or not label or name in seen:
            raise ValueError("平台模型能力表格式无效")
        seen.add(name)
        raw = item.get("capabilities") or {}
        if not isinstance(raw, dict):
            raise ValueError("平台模型能力表格式无效")
        capabilities = dict(_DEFAULT_CAPABILITIES)
        for key, value in raw.items():
            if key not in _CAPABILITY_KEYS or not isinstance(value, bool):
                raise ValueError("平台模型能力表格式无效")
            capabilities[key] = value
        models.append({"model": name, "label": label, "capabilities": capabilities})
    return {"version": 1, "models": models}


def _hosted_models() -> list[dict[str, Any]]:
    from src.server.auth.key2api import Key2ApiError, list_platform_models
    from src.tenancy.context import current_tenant

    user_id = current_tenant().user_id
    if not user_id:
        return []
    try:
        models = list_platform_models(user_id)
    except Key2ApiError:
        return []
    merged = []
    for item in models:
        model_id = str(item.get("id") or "")
        name = model_id[len(PLATFORM_PREFIX):] if model_id.startswith(PLATFORM_PREFIX) else model_id
        copied = dict(item)
        copied["capabilities"] = platform_capabilities(name)
        merged.append(copied)
    return merged


def _hosted_binding(model_config_id: str) -> RuntimeModelBinding:
    from src.server.auth.key2api import Key2ApiError, binding_for_platform_model
    from src.tenancy.context import current_tenant

    user_id = current_tenant().user_id
    if not user_id:
        raise CatalogError(_NO_PLATFORM_KEY)
    try:
        binding = binding_for_platform_model(model_config_id, user_id)
    except Key2ApiError as exc:
        if exc.status == 409:
            raise CatalogError(_NO_PLATFORM_KEY) from None
        raise CatalogError(str(exc)) from None
    return _apply_capabilities(binding)


def _local_models() -> list[dict[str, Any]]:
    try:
        from src.server.auth.cloud_client import list_models_sync
    except ImportError:
        return []
    return list_models_sync()


def _local_binding(model_config_id: str) -> RuntimeModelBinding:
    try:
        from src.server.auth.cloud_client import CloudSessionError, binding
    except ImportError:
        raise CatalogError(_LOCAL_LOGIN) from None
    try:
        return binding(model_config_id)
    except CloudSessionError as exc:
        raise CatalogError(str(exc)) from None


def _apply_capabilities(binding: RuntimeModelBinding) -> RuntimeModelBinding:
    primary = binding.primary
    caps = platform_capabilities(primary.entry.model)
    entry = primary.entry.model_copy(update={
        "capabilities": Capabilities(images=bool(caps.get("images", False))),
    })
    catalog = binding.catalog.model_copy(update={"models": (entry,)})
    return RuntimeModelBinding(catalog, (ResolvedModel(entry, primary.connection),))


def _capability_table() -> dict[str, dict[str, Any]]:
    path = shared_root() / "platform-models.json"
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(models, list):
        return {}
    table: dict[str, dict[str, Any]] = {}
    for item in models:
        if isinstance(item, dict) and item.get("model"):
            table[str(item["model"])] = item
    return table
