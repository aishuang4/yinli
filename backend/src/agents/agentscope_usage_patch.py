"""Runtime patches for AgentScope usage events.

AgentScope already extracts provider-side prompt cache usage into ChatUsage,
but its ModelCallEndEvent currently exposes only input/output tokens. This
patch preserves the original Agent reasoning flow and adds cache token fields
to the event metadata so the UI can display server-side prompt cache creation
and hit metrics.
"""

import inspect

from agentscope.agent import Agent
from agentscope.event import (
    ModelCallEndEvent,
    ModelCallStartEvent,
    TextBlockEndEvent,
    ThinkingBlockEndEvent,
    ToolCallEndEvent,
)
from agentscope.message import AssistantMsg, ToolCallBlock
from agentscope.message._base import Usage
from agentscope.model import ChatResponse


_PATCHED = False


async def _reasoning_impl_with_cache_usage(self, tool_choice=None):
    """AgentScope Agent._reasoning_impl patched to expose prompt-cache usage."""

    yield ModelCallStartEvent(
        reply_id=self.state.reply_id,
        model_name=self.model.model,
    )

    kwargs = await self._prepare_model_input()
    res = await self._call_model(
        tool_choice=tool_choice,
        **kwargs,
    )

    block_ids: dict = {"text": None, "thinking": None, "tools": []}
    completed_response: ChatResponse | None = None

    if inspect.isasyncgen(res):
        async for chunk in res:
            if chunk.is_last:
                completed_response = chunk
            else:
                async for evt in self._convert_chat_response_to_event(
                    block_ids,
                    chunk,
                ):
                    yield evt
    elif isinstance(res, ChatResponse):
        completed_response = res
        async for evt in self._convert_chat_response_to_event(
            block_ids,
            res,
        ):
            yield evt

    if completed_response is None:
        return

    if block_ids["text"] is not None:
        yield TextBlockEndEvent(
            reply_id=self.state.reply_id,
            block_id=block_ids["text"],
        )
    if block_ids["thinking"] is not None:
        yield ThinkingBlockEndEvent(
            reply_id=self.state.reply_id,
            block_id=block_ids["thinking"],
        )
    for tool_call_id in block_ids["tools"]:
        yield ToolCallEndEvent(
            reply_id=self.state.reply_id,
            tool_call_id=tool_call_id,
        )

    usage = completed_response.usage
    cache_creation_input_tokens = (
        usage.cache_creation_input_tokens if usage else 0
    )
    cache_input_tokens = usage.cache_input_tokens if usage else 0
    yield ModelCallEndEvent(
        reply_id=self.state.reply_id,
        input_tokens=usage.input_tokens if usage else 0,
        output_tokens=usage.output_tokens if usage else 0,
        metadata={
            "cache_creation_input_tokens": cache_creation_input_tokens,
            "cache_input_tokens": cache_input_tokens,
            "provider_cache": {
                "created_tokens": cache_creation_input_tokens,
                "hit_tokens": cache_input_tokens,
            },
        },
    )

    self._save_to_context(
        list(completed_response.content),
        completed_response.usage,
    )

    if not any(
        isinstance(_, ToolCallBlock) for _ in completed_response.content
    ):
        last_ctx = self._get_last_msg()
        final_usage = (
            Usage(
                input_tokens=last_ctx.usage.input_tokens,
                output_tokens=last_ctx.usage.output_tokens,
            )
            if last_ctx is not None and last_ctx.usage is not None
            else None
        )
        yield AssistantMsg(
            id=self.state.reply_id,
            name=self.name,
            content=list(completed_response.content),
            usage=final_usage,
        )


def patch_agentscope_usage_events() -> None:
    """Patch AgentScope once per process."""

    global _PATCHED
    if _PATCHED:
        return
    Agent._reasoning_impl = _reasoning_impl_with_cache_usage
    _PATCHED = True
