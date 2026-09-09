"""群聊工作室启动前校验。不接入 Chat RunContext。"""

from src.config.model_catalog import CatalogError
from src.config.runtime_settings import resolve_model_binding


def get_group_studio_model_error(model_config_id: str | None = None) -> str | None:
    """缺 API Key 时返回用户可读错误；可启动时返回 None。"""
    try:
        binding = resolve_model_binding(model_config_id)
    except CatalogError as exc:
        return str(exc)
    primary = binding.primary
    if primary.connection.requires_api_key and not primary.api_key.strip():
        return f"{primary.provider_name} API Key 未配置。请在系统设置中配置模型供应商和 API Key。"
    return None
