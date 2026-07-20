"""Runtime system settings persisted in SQLite."""

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from src.config.secret_store import (
    SecretStoreError,
    delete_model_api_key,
    get_model_api_key,
    set_model_api_key,
)
from src.config.settings import settings
from src.storage.database import get_json_record, upsert_json_record

SYSTEM_SETTINGS_NAMESPACE = "system_settings"
MODEL_SETTINGS_KEY = "model"


@dataclass(frozen=True)
class ModelProvider:
    id: str
    name: str
    default_base_url: str
    default_model: str
    models: tuple[str, ...] = ()
    requires_api_key: bool = True
    adapter: str = "openai"


MODEL_PROVIDERS: dict[str, ModelProvider] = {
    "dashscope": ModelProvider(
        id="dashscope",
        name="阿里云百炼 DashScope",
        default_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        default_model="qwen-plus",
        models=("qwen3.7-max", "qwen3.7-plus", "qwen-max", "qwen-plus", "qwen-turbo", "qwen-long"),
        adapter="dashscope",
    ),
    "openai": ModelProvider(
        id="openai",
        name="OpenAI",
        default_base_url="https://api.openai.com/v1",
        default_model="gpt-4.1",
        models=("gpt-4.1", "gpt-4.1-mini", "gpt-4o", "gpt-4o-mini", "o3", "o4-mini"),
        adapter="openai",
    ),
    "azure_openai": ModelProvider(
        id="azure_openai",
        name="Azure OpenAI",
        default_base_url="https://YOUR-RESOURCE.openai.azure.com/openai/deployments/YOUR-DEPLOYMENT",
        default_model="gpt-4.1",
        models=("gpt-4.1", "gpt-4.1-mini", "gpt-4o", "gpt-4o-mini"),
        adapter="openai",
    ),
    "deepseek": ModelProvider(
        id="deepseek",
        name="DeepSeek",
        default_base_url="https://api.deepseek.com",
        default_model="deepseek-v4-flash",
        models=("deepseek-v4-flash", "deepseek-v4-pro"),
        adapter="deepseek",
    ),
    "volcengine": ModelProvider(
        id="volcengine",
        name="火山引擎方舟",
        default_base_url="https://ark.cn-beijing.volces.com/api/v3",
        default_model="doubao-1-5-pro-32k-250115",
        models=("doubao-1-5-pro-32k-250115", "doubao-1-5-lite-32k-250115", "doubao-1-5-thinking-pro-250415"),
        adapter="openai",
    ),
    "siliconflow": ModelProvider(
        id="siliconflow",
        name="SiliconFlow",
        default_base_url="https://api.siliconflow.cn/v1",
        default_model="Qwen/Qwen2.5-72B-Instruct",
        models=("Qwen/Qwen2.5-72B-Instruct", "Qwen/Qwen2.5-32B-Instruct", "deepseek-ai/DeepSeek-V3", "deepseek-ai/DeepSeek-R1"),
        adapter="openai",
    ),
    "openrouter": ModelProvider(
        id="openrouter",
        name="OpenRouter",
        default_base_url="https://openrouter.ai/api/v1",
        default_model="openai/gpt-4.1",
        models=("openai/gpt-4.1", "openai/gpt-4o", "anthropic/claude-3.5-sonnet", "google/gemini-2.5-pro", "deepseek/deepseek-chat"),
        adapter="openai",
    ),
    "groq": ModelProvider(
        id="groq",
        name="Groq",
        default_base_url="https://api.groq.com/openai/v1",
        default_model="llama-3.3-70b-versatile",
        models=("llama-3.3-70b-versatile", "llama-3.1-8b-instant", "mixtral-8x7b-32768"),
        adapter="openai",
    ),
    "together": ModelProvider(
        id="together",
        name="Together AI",
        default_base_url="https://api.together.xyz/v1",
        default_model="meta-llama/Llama-3.3-70B-Instruct-Turbo",
        models=("meta-llama/Llama-3.3-70B-Instruct-Turbo", "meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo", "Qwen/Qwen2.5-72B-Instruct-Turbo"),
        adapter="openai",
    ),
    "perplexity": ModelProvider(
        id="perplexity",
        name="Perplexity",
        default_base_url="https://api.perplexity.ai",
        default_model="sonar-pro",
        models=("sonar-pro", "sonar", "sonar-reasoning-pro", "sonar-reasoning"),
        adapter="openai",
    ),
    "moonshot": ModelProvider(
        id="moonshot",
        name="Moonshot",
        default_base_url="https://api.moonshot.cn/v1",
        default_model="moonshot-v1-8k",
        models=("moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"),
        adapter="moonshot",
    ),
    "gemini": ModelProvider(
        id="gemini",
        name="Google Gemini",
        default_base_url="",
        default_model="gemini-2.0-flash",
        models=("gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-pro"),
        adapter="gemini",
    ),
    "anthropic": ModelProvider(
        id="anthropic",
        name="Anthropic",
        default_base_url="https://api.anthropic.com",
        default_model="claude-3-5-sonnet-latest",
        models=("claude-3-5-sonnet-latest", "claude-3-5-haiku-latest", "claude-3-opus-latest"),
        adapter="anthropic",
    ),
    "ollama": ModelProvider(
        id="ollama",
        name="Ollama",
        default_base_url="http://127.0.0.1:11434",
        default_model="llama3.1",
        models=("llama3.1", "qwen2.5", "deepseek-r1", "mistral"),
        requires_api_key=False,
        adapter="ollama",
    ),
    "custom_openai": ModelProvider(
        id="custom_openai",
        name="自定义 OpenAI 兼容",
        default_base_url="",
        default_model="",
        models=(),
        requires_api_key=False,
        adapter="openai",
    ),
}


@dataclass(frozen=True)
class EffectiveModelSettings:
    provider: str
    provider_name: str
    adapter: str
    api_key: str
    base_url: str
    default_model: str
    fallback_models: list[str]
    custom_models: list[str]


def _default_model_settings() -> dict[str, Any]:
    return {
        "provider": "deepseek",
        "providerConfigs": {
            "deepseek": {
                "providerName": MODEL_PROVIDERS["deepseek"].name,
                "apiKeyConfigured": bool(settings.deepseek_api_key),
                "baseUrl": settings.deepseek_base_url,
                "defaultModel": settings.default_model,
                "fallbackModels": settings.fallback_models,
                "customModels": [],
            },
            "dashscope": {
                "providerName": MODEL_PROVIDERS["dashscope"].name,
                "apiKeyConfigured": bool(settings.dashscope_api_key),
                "baseUrl": settings.dashscope_base_url,
                "defaultModel": MODEL_PROVIDERS["dashscope"].default_model,
                "fallbackModels": ["qwen-turbo", "qwen-long"],
                "customModels": [],
            }
        },
    }


def _clean_model_list(items: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items or []:
        model = str(item).strip()
        if not model or model in seen:
            continue
        seen.add(model)
        result.append(model)
    return result


def _default_provider_api_key(provider_id: str) -> str:
    if provider_id == "deepseek":
        return settings.deepseek_api_key
    if provider_id == "dashscope":
        return settings.dashscope_api_key
    return ""


def _default_provider_config(provider: ModelProvider) -> dict[str, Any]:
    return {
        "providerName": provider.name,
        "apiKeyConfigured": bool(_default_provider_api_key(provider.id)),
        "baseUrl": provider.default_base_url,
        "defaultModel": provider.default_model,
        "fallbackModels": [],
        "customModels": [],
    }


def _provider_configs_from_stored(stored: dict[str, Any], defaults: dict[str, Any]) -> dict[str, dict[str, Any]]:
    provider_configs = stored.get("providerConfigs")
    if isinstance(provider_configs, dict):
        result: dict[str, dict[str, Any]] = {}
        for provider_id, provider in MODEL_PROVIDERS.items():
            raw = provider_configs.get(provider_id) or {}
            if not isinstance(raw, dict):
                raw = {}
            base = _default_provider_config(provider)
            result[provider_id] = {
                "providerName": str(raw.get("providerName") or base["providerName"]),
                "apiKeyConfigured": bool(
                    raw.get("apiKeyConfigured")
                    or base["apiKeyConfigured"]
                ),
                "baseUrl": str(raw.get("baseUrl") or base["baseUrl"]),
                "defaultModel": str(raw.get("defaultModel") or base["defaultModel"]),
                "fallbackModels": _clean_model_list(raw.get("fallbackModels") or []),
                "customModels": _clean_model_list(raw.get("customModels") or []),
            }
        return result

    # Backward compatibility for the previous single-provider settings shape.
    provider_id = str(stored.get("provider") or defaults["provider"])
    provider = MODEL_PROVIDERS.get(provider_id, MODEL_PROVIDERS["custom_openai"])
    result = {
        item.id: _default_provider_config(item)
        for item in MODEL_PROVIDERS.values()
    }
    result[provider.id] = {
        "providerName": str(stored.get("providerName") or provider.name),
        "apiKeyConfigured": bool(
            stored.get("apiKeyConfigured")
            or _default_provider_api_key(provider.id)
        ),
        "baseUrl": str(stored.get("baseUrl") or provider.default_base_url),
        "defaultModel": str(stored.get("defaultModel") or provider.default_model),
        "fallbackModels": _clean_model_list(stored.get("fallbackModels") or settings.fallback_models or []),
        "customModels": _clean_model_list(stored.get("customModels") or []),
    }
    return result


def _migrate_legacy_api_keys(stored: dict[str, Any]) -> dict[str, Any]:
    """将历史 SQLite 明文 Key 移入系统凭据库，再清除原字段。"""

    migrated = deepcopy(stored)
    changed = False
    provider_configs = migrated.get("providerConfigs")
    if isinstance(provider_configs, dict):
        for provider_id, raw in provider_configs.items():
            if not isinstance(raw, dict) or "apiKey" not in raw:
                continue
            legacy_key = str(raw.get("apiKey") or "").strip()
            if legacy_key:
                set_model_api_key(str(provider_id), legacy_key)
            raw.pop("apiKey", None)
            raw["apiKeyConfigured"] = bool(
                legacy_key
                or raw.get("apiKeyConfigured")
                or _default_provider_api_key(str(provider_id))
            )
            changed = True

    if "apiKey" in migrated:
        provider_id = str(migrated.get("provider") or "deepseek")
        legacy_key = str(migrated.get("apiKey") or "").strip()
        if legacy_key:
            set_model_api_key(provider_id, legacy_key)
        migrated.pop("apiKey", None)
        migrated["apiKeyConfigured"] = bool(
            legacy_key
            or migrated.get("apiKeyConfigured")
            or _default_provider_api_key(provider_id)
        )
        changed = True

    if changed:
        upsert_json_record(SYSTEM_SETTINGS_NAMESPACE, MODEL_SETTINGS_KEY, migrated)
    return migrated


def get_model_settings_raw() -> dict[str, Any]:
    stored = get_json_record(SYSTEM_SETTINGS_NAMESPACE, MODEL_SETTINGS_KEY) or {}
    try:
        stored = _migrate_legacy_api_keys(stored)
    except SecretStoreError as exc:
        raise ValueError("旧版 API Key 无法迁移到系统安全凭据库。") from exc
    defaults = _default_model_settings()
    provider_id = str(stored.get("provider") or defaults["provider"])
    provider = MODEL_PROVIDERS.get(provider_id, MODEL_PROVIDERS["custom_openai"])
    provider_configs = _provider_configs_from_stored(stored, defaults)
    current = provider_configs[provider.id]
    try:
        api_key = get_model_api_key(provider.id) or _default_provider_api_key(provider.id)
    except SecretStoreError as exc:
        raise ValueError("无法读取系统安全凭据库中的 API Key。") from exc
    current["apiKeyConfigured"] = bool(api_key)
    return {
        "provider": provider.id,
        "providerName": current["providerName"],
        "adapter": provider.adapter,
        "apiKey": api_key,
        "baseUrl": current["baseUrl"],
        "defaultModel": current["defaultModel"],
        "fallbackModels": current["fallbackModels"],
        "customModels": current["customModels"],
        "providerConfigs": provider_configs,
    }


def get_effective_model_settings() -> EffectiveModelSettings:
    raw = get_model_settings_raw()
    return EffectiveModelSettings(
        provider=raw["provider"],
        provider_name=raw["providerName"],
        adapter=raw["adapter"],
        api_key=raw["apiKey"],
        base_url=raw["baseUrl"],
        default_model=raw["defaultModel"],
        fallback_models=raw["fallbackModels"],
        custom_models=raw["customModels"],
    )


def get_current_model_options() -> list[str]:
    raw = get_model_settings_raw()
    provider = MODEL_PROVIDERS[raw["provider"]]
    if provider.requires_api_key and not raw["apiKey"]:
        return []
    return _clean_model_list([
        *provider.models,
        raw["defaultModel"],
        *raw["fallbackModels"],
        *raw["customModels"],
    ])


def _is_provider_configured(provider: ModelProvider, config: dict[str, Any]) -> bool:
    if provider.id == "custom_openai":
        return bool(
            str(config.get("baseUrl") or "").strip()
            and (
                str(config.get("defaultModel") or "").strip()
                or _clean_model_list(config.get("customModels") or [])
            )
        )
    return bool(
        not provider.requires_api_key
        or config.get("apiKeyConfigured")
    )


def get_model_settings_public() -> dict[str, Any]:
    raw = get_model_settings_raw()
    provider = MODEL_PROVIDERS[raw["provider"]]
    try:
        for item in MODEL_PROVIDERS.values():
            config = raw["providerConfigs"].get(item.id, {})
            config["apiKeyConfigured"] = bool(
                get_model_api_key(item.id)
                or _default_provider_api_key(item.id)
            )
    except SecretStoreError as exc:
        raise ValueError("无法读取系统安全凭据库中的 API Key。") from exc
    return {
        "provider": raw["provider"],
        "providerName": raw["providerName"],
        "baseUrl": raw["baseUrl"],
        "defaultModel": raw["defaultModel"],
        "fallbackModels": raw["fallbackModels"],
        "customModels": raw["customModels"],
        "modelOptions": get_current_model_options(),
        "apiKeyConfigured": bool(raw["apiKey"]),
        "apiKeyPreview": "已安全保存" if raw["apiKey"] else "",
        "requiresApiKey": provider.requires_api_key,
        "adapter": provider.adapter,
        "providers": [
            {
                "id": item.id,
                "name": item.name,
                "defaultBaseUrl": item.default_base_url,
                "defaultModel": item.default_model,
                "models": list(item.models),
                "requiresApiKey": item.requires_api_key,
                "adapter": item.adapter,
                "configured": _is_provider_configured(item, raw["providerConfigs"].get(item.id, {})),
                "apiKeyConfigured": bool(raw["providerConfigs"].get(item.id, {}).get("apiKeyConfigured")),
                "apiKeyPreview": "已安全保存" if raw["providerConfigs"].get(item.id, {}).get("apiKeyConfigured") else "",
                "providerName": str(raw["providerConfigs"].get(item.id, {}).get("providerName") or item.name),
                "baseUrl": str(raw["providerConfigs"].get(item.id, {}).get("baseUrl") or item.default_base_url),
                "configuredDefaultModel": str(raw["providerConfigs"].get(item.id, {}).get("defaultModel") or item.default_model),
                "fallbackModels": list(raw["providerConfigs"].get(item.id, {}).get("fallbackModels") or []),
                "customModels": list(raw["providerConfigs"].get(item.id, {}).get("customModels") or []),
            }
            for item in MODEL_PROVIDERS.values()
        ],
    }


def save_model_settings(data: dict[str, Any]) -> dict[str, Any]:
    stored = get_json_record(SYSTEM_SETTINGS_NAMESPACE, MODEL_SETTINGS_KEY) or {}
    try:
        stored = _migrate_legacy_api_keys(stored)
    except SecretStoreError as exc:
        raise ValueError("旧版 API Key 无法迁移到系统安全凭据库。") from exc
    current = get_model_settings_raw()
    provider_id = str(data.get("provider") or current["provider"])
    provider = MODEL_PROVIDERS.get(provider_id, MODEL_PROVIDERS["custom_openai"])
    provider_id = provider.id
    provider_configs = _provider_configs_from_stored(stored, _default_model_settings())
    existing = provider_configs.get(provider_id) or _default_provider_config(provider)
    next_settings = {
        "provider": provider_id,
        "providerConfigs": provider_configs,
    }
    next_config = {
        "providerName": str(data.get("providerName") or existing.get("providerName") or provider.name),
        "apiKeyConfigured": bool(existing.get("apiKeyConfigured")),
        "baseUrl": str(data.get("baseUrl") or existing.get("baseUrl") or provider.default_base_url),
        "defaultModel": str(data.get("defaultModel") or existing.get("defaultModel") or provider.default_model),
        "fallbackModels": _clean_model_list(data.get("fallbackModels") or []),
        "customModels": _clean_model_list(data.get("customModels") or []),
    }
    try:
        if data.get("clearApiKey"):
            delete_model_api_key(provider_id)
            next_config["apiKeyConfigured"] = bool(_default_provider_api_key(provider_id))
        elif str(data.get("apiKey") or "").strip():
            set_model_api_key(provider_id, str(data["apiKey"]))
            next_config["apiKeyConfigured"] = True
    except SecretStoreError as exc:
        raise ValueError("API Key 无法写入系统安全凭据库。") from exc

    provider_configs[provider_id] = next_config
    upsert_json_record(SYSTEM_SETTINGS_NAMESPACE, MODEL_SETTINGS_KEY, next_settings)
    return get_model_settings_public()
