"""Runtime system settings persisted in SQLite."""

from dataclasses import dataclass
from typing import Any

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
    requires_api_key: bool = True
    adapter: str = "openai"


MODEL_PROVIDERS: dict[str, ModelProvider] = {
    "dashscope": ModelProvider(
        id="dashscope",
        name="阿里云百炼 DashScope",
        default_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        default_model="qwen-plus",
        adapter="dashscope",
    ),
    "openai": ModelProvider(
        id="openai",
        name="OpenAI",
        default_base_url="https://api.openai.com/v1",
        default_model="gpt-4.1",
        adapter="openai",
    ),
    "azure_openai": ModelProvider(
        id="azure_openai",
        name="Azure OpenAI",
        default_base_url="https://YOUR-RESOURCE.openai.azure.com/openai/deployments/YOUR-DEPLOYMENT",
        default_model="gpt-4.1",
        adapter="openai",
    ),
    "deepseek": ModelProvider(
        id="deepseek",
        name="DeepSeek",
        default_base_url="https://api.deepseek.com",
        default_model="deepseek-chat",
        adapter="deepseek",
    ),
    "volcengine": ModelProvider(
        id="volcengine",
        name="火山引擎方舟",
        default_base_url="https://ark.cn-beijing.volces.com/api/v3",
        default_model="doubao-1-5-pro-32k-250115",
        adapter="openai",
    ),
    "siliconflow": ModelProvider(
        id="siliconflow",
        name="SiliconFlow",
        default_base_url="https://api.siliconflow.cn/v1",
        default_model="Qwen/Qwen2.5-72B-Instruct",
        adapter="openai",
    ),
    "openrouter": ModelProvider(
        id="openrouter",
        name="OpenRouter",
        default_base_url="https://openrouter.ai/api/v1",
        default_model="openai/gpt-4.1",
        adapter="openai",
    ),
    "groq": ModelProvider(
        id="groq",
        name="Groq",
        default_base_url="https://api.groq.com/openai/v1",
        default_model="llama-3.3-70b-versatile",
        adapter="openai",
    ),
    "together": ModelProvider(
        id="together",
        name="Together AI",
        default_base_url="https://api.together.xyz/v1",
        default_model="meta-llama/Llama-3.3-70B-Instruct-Turbo",
        adapter="openai",
    ),
    "perplexity": ModelProvider(
        id="perplexity",
        name="Perplexity",
        default_base_url="https://api.perplexity.ai",
        default_model="sonar-pro",
        adapter="openai",
    ),
    "moonshot": ModelProvider(
        id="moonshot",
        name="Moonshot",
        default_base_url="https://api.moonshot.cn/v1",
        default_model="moonshot-v1-8k",
        adapter="moonshot",
    ),
    "gemini": ModelProvider(
        id="gemini",
        name="Google Gemini",
        default_base_url="",
        default_model="gemini-2.0-flash",
        adapter="gemini",
    ),
    "anthropic": ModelProvider(
        id="anthropic",
        name="Anthropic",
        default_base_url="https://api.anthropic.com",
        default_model="claude-3-5-sonnet-latest",
        adapter="anthropic",
    ),
    "ollama": ModelProvider(
        id="ollama",
        name="Ollama",
        default_base_url="http://127.0.0.1:11434",
        default_model="llama3.1",
        requires_api_key=False,
        adapter="ollama",
    ),
    "custom_openai": ModelProvider(
        id="custom_openai",
        name="自定义 OpenAI 兼容",
        default_base_url="",
        default_model="",
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


def _mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}...{value[-4:]}"


def _default_model_settings() -> dict[str, Any]:
    return {
        "provider": "dashscope",
        "apiKey": settings.dashscope_api_key,
        "baseUrl": settings.dashscope_base_url,
        "defaultModel": settings.default_model,
        "fallbackModels": settings.fallback_models,
    }


def get_model_settings_raw() -> dict[str, Any]:
    stored = get_json_record(SYSTEM_SETTINGS_NAMESPACE, MODEL_SETTINGS_KEY) or {}
    defaults = _default_model_settings()
    provider_id = str(stored.get("provider") or defaults["provider"])
    provider = MODEL_PROVIDERS.get(provider_id, MODEL_PROVIDERS["custom_openai"])
    return {
        "provider": provider.id,
        "providerName": str(stored.get("providerName") or provider.name),
        "adapter": provider.adapter,
        "apiKey": str(stored.get("apiKey") or defaults["apiKey"] or ""),
        "baseUrl": str(stored.get("baseUrl") or provider.default_base_url),
        "defaultModel": str(stored.get("defaultModel") or provider.default_model),
        "fallbackModels": list(stored.get("fallbackModels") or defaults["fallbackModels"] or []),
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
    )


def get_model_settings_public() -> dict[str, Any]:
    raw = get_model_settings_raw()
    provider = MODEL_PROVIDERS[raw["provider"]]
    return {
        "provider": raw["provider"],
        "providerName": raw["providerName"],
        "baseUrl": raw["baseUrl"],
        "defaultModel": raw["defaultModel"],
        "fallbackModels": raw["fallbackModels"],
        "apiKeyConfigured": bool(raw["apiKey"]),
        "apiKeyPreview": _mask_secret(raw["apiKey"]),
        "requiresApiKey": provider.requires_api_key,
        "adapter": provider.adapter,
        "providers": [
            {
                "id": item.id,
                "name": item.name,
                "defaultBaseUrl": item.default_base_url,
                "defaultModel": item.default_model,
                "requiresApiKey": item.requires_api_key,
                "adapter": item.adapter,
            }
            for item in MODEL_PROVIDERS.values()
        ],
    }


def save_model_settings(data: dict[str, Any]) -> dict[str, Any]:
    current = get_model_settings_raw()
    provider_id = str(data.get("provider") or current["provider"])
    provider = MODEL_PROVIDERS.get(provider_id, MODEL_PROVIDERS["custom_openai"])
    provider_id = provider.id
    next_settings = {
        "provider": provider_id,
        "providerName": str(data.get("providerName") or provider.name),
        "apiKey": current["apiKey"],
        "baseUrl": str(data.get("baseUrl") or provider.default_base_url),
        "defaultModel": str(data.get("defaultModel") or provider.default_model),
        "fallbackModels": [
            str(item).strip()
            for item in (data.get("fallbackModels") or [])
            if str(item).strip()
        ],
    }
    if data.get("clearApiKey"):
        next_settings["apiKey"] = ""
    elif "apiKey" in data and data.get("apiKey") is not None:
        next_settings["apiKey"] = str(data.get("apiKey") or "")

    upsert_json_record(SYSTEM_SETTINGS_NAMESPACE, MODEL_SETTINGS_KEY, next_settings)
    return get_model_settings_public()
