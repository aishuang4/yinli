"""AgentScope 模型调用 usage 扩展中间件。"""

import inspect
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any

from agentscope.agent import Agent
from agentscope.event import ModelCallEndEvent
from agentscope.middleware import MiddlewareBase
from agentscope.model import ChatResponse


class ProviderCacheUsageMiddleware(MiddlewareBase):
    """把供应商 prompt cache usage 附加到 ModelCallEndEvent。"""

    def __init__(self) -> None:
        self._usage_by_reply: dict[tuple[int, str], dict[str, int]] = {}

    async def on_model_call(
        self,
        agent: Agent,
        input_kwargs: dict,
        next_handler: Callable[
            ...,
            Awaitable[ChatResponse | AsyncGenerator[ChatResponse, None]],
        ],
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        """记录完整响应 usage，不改变模型响应或流式顺序。"""

        response = await next_handler(**input_kwargs)
        if inspect.isasyncgen(response):
            return self._capture_stream(agent, response)
        self._remember_usage(agent, response)
        return response

    async def on_reasoning(
        self,
        agent: Agent,
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        """只扩展结束事件 metadata，保留 AgentScope 原生 reasoning 实现。"""

        key = self._usage_key(agent)
        try:
            async for event in next_handler(**input_kwargs):
                if isinstance(event, ModelCallEndEvent):
                    usage = self._usage_by_reply.get(key, {})
                    created_tokens = int(usage.get("cache_creation_input_tokens") or 0)
                    hit_tokens = int(usage.get("cache_input_tokens") or 0)
                    metadata = {
                        **event.metadata,
                        "cache_creation_input_tokens": created_tokens,
                        "cache_input_tokens": hit_tokens,
                        "provider_cache": {
                            "created_tokens": created_tokens,
                            "hit_tokens": hit_tokens,
                        },
                    }
                    event = event.model_copy(update={"metadata": metadata})
                yield event
        finally:
            self._usage_by_reply.pop(key, None)

    async def _capture_stream(
        self,
        agent: Agent,
        response: AsyncGenerator[ChatResponse, None],
    ) -> AsyncGenerator[ChatResponse, None]:
        """透传流式响应，并从最终 chunk 读取 usage。"""

        async for chunk in response:
            if chunk.is_last:
                self._remember_usage(agent, chunk)
            yield chunk

    def _remember_usage(self, agent: Agent, response: ChatResponse) -> None:
        usage = response.usage
        self._usage_by_reply[self._usage_key(agent)] = {
            "cache_creation_input_tokens": (
                int(usage.cache_creation_input_tokens or 0) if usage else 0
            ),
            "cache_input_tokens": int(usage.cache_input_tokens or 0) if usage else 0,
        }

    @staticmethod
    def _usage_key(agent: Agent) -> tuple[int, str]:
        return id(agent), str(agent.state.reply_id)
