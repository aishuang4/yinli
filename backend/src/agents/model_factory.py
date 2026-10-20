"""AgentScope 模型创建与应用层降级链。"""

from typing import Any, AsyncGenerator

from pydantic import SecretStr

from agentscope.model import (
    AnthropicChatModel,
    ChatModelBase,
    ChatResponse,
    DashScopeChatModel,
    DeepSeekChatModel,
    GeminiChatModel,
    MoonshotChatModel,
    OllamaChatModel,
    OpenAIChatModel,
)
from agentscope.credential import DashScopeCredential
from agentscope.credential import (
    AnthropicCredential,
    DeepSeekCredential,
    GeminiCredential,
    MoonshotCredential,
    OllamaCredential,
    OpenAICredential,
)
from agentscope.tool import ToolChoice

from src.agents.openai_message_formatter import (
    UserNameOnlyMoonshotFormatter,
    UserNameOnlyOpenAIFormatter,
)
from src.agents.runtime_types import AgentModelOptions, FallbackEventCallback
from src.config.runtime_settings import ResolvedModel, binding_for_model


# 各 adapter 的默认上下文窗口（tokens）。AgentScope 默认 32768 会让 128k 模型在用掉四分之一时就触发压缩；
# 这里按当前主流模型给保守默认值，用户可在 models.json 的 contextSize 覆盖。
DEFAULT_CONTEXT_SIZE_BY_ADAPTER: dict[str, int] = {
    "deepseek": 128_000,
    "dashscope": 131_072,
    "openai": 128_000,
    "moonshot": 128_000,
    "anthropic": 200_000,
    "gemini": 1_000_000,
    "ollama": 32_768,
}
FALLBACK_CONTEXT_SIZE = 32_768


def resolve_context_size(resolved: ResolvedModel) -> int:
    """模型条目显式配置优先，否则按 adapter 默认；都没有时回到 AgentScope 默认 32k。"""

    configured = int(getattr(resolved.entry, "context_size", 0) or 0)
    if configured > 0:
        return configured
    return DEFAULT_CONTEXT_SIZE_BY_ADAPTER.get(str(resolved.adapter), FALLBACK_CONTEXT_SIZE)


def create_model(
    model_name: str,
    stream: bool = True,
    thinking_enable: bool = False,
    model_options: AgentModelOptions | None = None,
    resolved_model: ResolvedModel | None = None,
    max_retries: int = 3,
):
    """
    创建当前运行供应商的聊天模型实例。

    Args:
        model_name: 模型名称（如 deepseek-chat、gpt-4.1、本地 OpenAI 兼容模型名）
        stream: 是否启用流式输出

    Returns:
        ChatModelBase: 配置好的模型实例
    """

    runtime = resolved_model or binding_for_model(model_name).primary
    model_name = runtime.default_model
    configured = runtime.entry.parameters
    parameters: dict[str, object] = {
        "thinking_enable": thinking_enable,
        "temperature": configured.temperature,
        "top_p": configured.top_p,
        "max_tokens": configured.max_tokens,
    }
    if model_options is not None:
        for name in ("temperature", "top_p", "max_tokens"):
            value = getattr(model_options, name)
            if value is not None:
                parameters[name] = value
    api_key = SecretStr(runtime.api_key)
    base_url = runtime.base_url or None
    context_size = resolve_context_size(runtime)

    if runtime.adapter == "openai":
        return OpenAIChatModel(
            credential=OpenAICredential(api_key=api_key, base_url=base_url),
            model=model_name,
            parameters=OpenAIChatModel.Parameters(**parameters),
            stream=stream,
            max_retries=max_retries,
            context_size=context_size,
            formatter=UserNameOnlyOpenAIFormatter(),
        )
    if runtime.adapter == "deepseek":
        return DeepSeekChatModel(
            credential=DeepSeekCredential(api_key=api_key, base_url=runtime.base_url or "https://api.deepseek.com"),
            model=model_name,
            parameters=DeepSeekChatModel.Parameters(**parameters),
            stream=stream,
            max_retries=max_retries,
            context_size=context_size,
        )
    if runtime.adapter == "moonshot":
        return MoonshotChatModel(
            credential=MoonshotCredential(api_key=api_key, base_url=runtime.base_url or "https://api.moonshot.cn/v1"),
            model=model_name,
            parameters=MoonshotChatModel.Parameters(**parameters),
            stream=stream,
            max_retries=max_retries,
            context_size=context_size,
            formatter=UserNameOnlyMoonshotFormatter(),
        )
    if runtime.adapter == "gemini":
        return GeminiChatModel(
            credential=GeminiCredential(api_key=api_key),
            client_kwargs={"http_options": {"base_url": base_url}} if base_url else {},
            model=model_name,
            parameters=GeminiChatModel.Parameters(**parameters),
            stream=stream,
            max_retries=max_retries,
            context_size=context_size,
        )
    if runtime.adapter == "anthropic":
        anthropic_parameters = {
            key: value
            for key, value in parameters.items()
            if key in AnthropicChatModel.Parameters.model_fields
        }
        return AnthropicChatModel(
            credential=AnthropicCredential(api_key=api_key, base_url=base_url),
            model=model_name,
            parameters=AnthropicChatModel.Parameters(**anthropic_parameters),
            stream=stream,
            max_retries=max_retries,
            context_size=context_size,
        )
    if runtime.adapter == "ollama":
        return OllamaChatModel(
            credential=OllamaCredential(host=runtime.base_url or None),
            model=model_name,
            parameters=OllamaChatModel.Parameters(**parameters),
            stream=stream,
            max_retries=max_retries,
            context_size=context_size,
        )

    return DashScopeChatModel(
        credential=DashScopeCredential(
            api_key=api_key,
            base_url=runtime.base_url,
        ),
        model=model_name,
        parameters=DashScopeChatModel.Parameters(**parameters),
        stream=stream,
        max_retries=max_retries,
        context_size=context_size,
    )


class FallbackChainModel(ChatModelBase):
    """Application-level multi-step fallback model chain."""

    def __init__(
        self,
        models: list[ChatModelBase],
        on_event: FallbackEventCallback | None = None,
    ) -> None:
        if not models:
            raise ValueError("FallbackChainModel requires at least one model")
        self.models = models
        self.on_event = on_event
        super().__init__(
            credential=models[0].credential,
            model=" -> ".join(model.model for model in models),
            parameters=models[0].parameters,
            stream=models[0].stream,
            max_retries=0,
            # 降级链任何一环都可能真正承接请求，压缩阈值必须按链上最小窗口算。
            context_size=min(
                int(getattr(model, "context_size", 0) or FALLBACK_CONTEXT_SIZE) for model in models
            ),
        )

    async def _call_api(
        self,
        model_name: str,
        messages: list,
        tools: list[dict] | None = None,
        tool_choice: ToolChoice | None = None,
        **kwargs: Any,
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        """按 AgentScope ChatModelBase 协议执行模型降级链。"""

        last_error: Exception | None = None
        total = len(self.models)
        for index, model in enumerate(self.models):
            self._emit({
                "type": "attempt",
                "model": model.model,
                "index": index,
                "total": total,
            })
            try:
                response = await model(
                    messages=messages,
                    tools=tools,
                    tool_choice=tool_choice,
                    **kwargs,
                )
                self._emit({
                    "type": "success",
                    "model": model.model,
                    "index": index,
                    "total": total,
                })
                return response
            except Exception as exc:
                last_error = exc
                self._emit({
                    "type": "failure",
                    "model": model.model,
                    "index": index,
                    "total": total,
                    "error": type(exc).__name__,
                })
        if last_error:
            raise last_error
        raise RuntimeError("Fallback chain failed without an exception")

    def _emit(self, event: dict[str, Any]) -> None:
        if not self.on_event:
            return
        try:
            self.on_event(event)
        except Exception:
            pass


def create_model_chain(
    model_names: list[str],
    thinking_enable: bool = False,
    model_options: AgentModelOptions | None = None,
    on_fallback_event: FallbackEventCallback | None = None,
    resolved_models: tuple[ResolvedModel, ...] | None = None,
) -> ChatModelBase:
    """Create a primary + fallback chain as one AgentScope-compatible model."""

    chain = resolved_models or binding_for_model(model_names[0] if model_names else None).chain
    _assert_public_model_urls(chain)
    models = [
        create_model(
            item.default_model,
            stream=True,
            thinking_enable=thinking_enable,
            model_options=model_options,
            resolved_model=item,
        )
        for item in chain
    ]
    if len(models) == 1:
        return models[0]
    return FallbackChainModel(models, on_event=on_fallback_event)


def _assert_public_model_urls(chain: tuple[ResolvedModel, ...]) -> None:
    """调用前再查一次地址，挡住保存之后才解析到内网的域名。"""

    from src.config.model_catalog import CatalogError
    from src.security.egress import EgressBlocked, assert_public_url_sync, egress_enforced

    if not egress_enforced():
        return
    for item in chain:
        if not item.base_url.strip():
            continue
        try:
            assert_public_url_sync(item.base_url, purpose="model")
        except EgressBlocked as exc:
            raise CatalogError("模型地址不能指向内网或本机") from exc
