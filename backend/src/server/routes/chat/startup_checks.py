"""Chat Run 启动前校验。"""

from src.config.runtime_settings import MODEL_PROVIDERS
from src.server.routes.chat.models import MULTIMODAL_MODELS
from src.server.routes.chat.run_context import ChatRunContext


def get_startup_error(ctx: ChatRunContext, run_data) -> str | None:
    """返回启动阶段阻塞错误；无错误返回 None。"""

    if run_data.images and ctx.model_name not in MULTIMODAL_MODELS:
        return f"{ctx.model_name} 不支持图片输入，请切换到 qwen3.7-plus。"

    provider = MODEL_PROVIDERS.get(ctx.runtime_model_settings.provider)
    if provider and provider.requires_api_key and not ctx.runtime_model_settings.api_key:
        return f"{provider.name} API Key 未配置。请在系统设置中配置模型供应商和 API Key。"

    return None
