"""群聊工作室启动前校验。不接入 Chat RunContext。"""

from src.config.runtime_settings import MODEL_PROVIDERS, get_effective_model_settings


def get_group_studio_model_error() -> str | None:
    """缺 API Key 时返回用户可读错误；可启动时返回 None。"""

    runtime = get_effective_model_settings()
    provider = MODEL_PROVIDERS.get(runtime.provider)
    if provider and provider.requires_api_key and not str(runtime.api_key or "").strip():
        return f"{provider.name} API Key 未配置。请在系统设置中配置模型供应商和 API Key。"
    return None
