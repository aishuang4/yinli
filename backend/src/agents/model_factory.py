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

from src.agents.runtime_types import AgentModelOptions, FallbackEventCallback
from src.config.runtime_settings import get_effective_model_settings
from src.config.settings import settings


def create_model(
    model_name: str,
    stream: bool = True,
    thinking_enable: bool = False,
    model_options: AgentModelOptions | None = None,
):
    """
    创建当前运行供应商的聊天模型实例。

    Args:
        model_name: 模型名称（如 deepseek-chat、gpt-4.1、本地 OpenAI 兼容模型名）
        stream: 是否启用流式输出

    Returns:
        ChatModelBase: 配置好的模型实例
    """

    parameters: dict[str, object] = {
        "thinking_enable": thinking_enable,
    }
    if model_options:
        if model_options.temperature is not None:
            parameters["temperature"] = model_options.temperature
        if model_options.top_p is not None:
            parameters["top_p"] = model_options.top_p
        if model_options.max_tokens is not None:
            parameters["max_tokens"] = model_options.max_tokens

    runtime = get_effective_model_settings()
    api_key = SecretStr(runtime.api_key)
    base_url = runtime.base_url or None

    if runtime.adapter == "openai":
        return OpenAIChatModel(
            credential=OpenAICredential(api_key=api_key, base_url=base_url),
            model=model_name,
            parameters=OpenAIChatModel.Parameters(**parameters),
            stream=stream,
            max_retries=3,
        )
    if runtime.adapter == "deepseek":
        return DeepSeekChatModel(
            credential=DeepSeekCredential(api_key=api_key, base_url=runtime.base_url or "https://api.deepseek.com"),
            model=model_name,
            parameters=DeepSeekChatModel.Parameters(**parameters),
            stream=stream,
            max_retries=3,
        )
    if runtime.adapter == "moonshot":
        return MoonshotChatModel(
            credential=MoonshotCredential(api_key=api_key, base_url=runtime.base_url or "https://api.moonshot.cn/v1"),
            model=model_name,
            parameters=MoonshotChatModel.Parameters(**parameters),
            stream=stream,
            max_retries=3,
        )
    if runtime.adapter == "gemini":
        return GeminiChatModel(
            credential=GeminiCredential(api_key=api_key),
            model=model_name,
            parameters=GeminiChatModel.Parameters(**parameters),
            stream=stream,
            max_retries=3,
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
            max_retries=3,
        )
    if runtime.adapter == "ollama":
        return OllamaChatModel(
            credential=OllamaCredential(host=runtime.base_url or None),
            model=model_name,
            parameters=OllamaChatModel.Parameters(**parameters),
            stream=stream,
            max_retries=3,
        )

    return DashScopeChatModel(
        credential=DashScopeCredential(
            api_key=api_key,
            base_url=runtime.base_url or settings.dashscope_base_url,
        ),
        model=model_name,
        parameters=DashScopeChatModel.Parameters(**parameters),
        stream=stream,
        max_retries=3,
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
            context_size=models[0].context_size,
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
                    "error": str(exc),
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
) -> ChatModelBase:
    """Create a primary + fallback chain as one AgentScope-compatible model."""

    cleaned_names: list[str] = []
    seen_names: set[str] = set()
    for raw_name in model_names:
        name = str(raw_name).strip()
        if not name or name in seen_names:
            continue
        seen_names.add(name)
        cleaned_names.append(name)
    if not cleaned_names:
        raise ValueError("At least one model name is required")
    models = [
        create_model(
            name,
            stream=True,
            thinking_enable=thinking_enable,
            model_options=model_options,
        )
        for name in cleaned_names
    ]
    if len(models) == 1:
        return models[0]
    return FallbackChainModel(models, on_event=on_fallback_event)
