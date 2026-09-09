"""Chat Run 启动前校验。"""

from src.config.legacy_model_settings import MODEL_PROVIDERS
from src.config.runtime_settings import current_model_binding
from src.server.routes.chat.run_context import ChatRunContext


def get_startup_error(ctx: ChatRunContext, run_data) -> str | None:
    """返回启动阶段阻塞错误；无错误返回 None。"""

    if not run_data.message.strip():
        return "请输入要执行的任务。"

    binding = current_model_binding()
    if run_data.images and (not binding or not binding.primary.entry.capabilities.images):
        return "所选模型未启用图片能力，请切换模型。"

    provider = MODEL_PROVIDERS.get(ctx.runtime_model_settings.provider)
    if provider and provider.requires_api_key and not ctx.runtime_model_settings.api_key:
        return f"{provider.name} API Key 未配置。请在系统设置中配置模型供应商和 API Key。"

    return None
