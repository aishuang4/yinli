"""独立审核的 SDK 传输接点；无 Agent 缓存、工具、历史、降级或自动重试。"""
from __future__ import annotations


import asyncio
from dataclasses import dataclass

from agentscope.message import SystemMsg, UserMsg

from src.agents.model_factory import create_model
from src.agents.model_options import AgentModelOptions
from src.config.runtime_settings import RuntimeModelBinding, model_binding_scope


@dataclass(frozen=True)
class ReviewModelResponse:
    """只交付最终文本与用量；不交付模型思维链。"""

    text: str
    input_tokens: int = 0
    output_tokens: int = 0


async def call_permission_review_model(
    binding: RuntimeModelBinding, *, system: str, prompt: str, max_output_tokens: int,
) -> ReviewModelResponse:
    """每次新实例，ContextVar 在正常、失败和取消时均恢复父绑定。"""
    with model_binding_scope(binding):
        model = create_model(binding.primary.default_model, resolved_model=binding.primary,
                             stream=False, thinking_enable=False, max_retries=0,
                             model_options=AgentModelOptions(temperature=0, max_tokens=max_output_tokens))
        response = await model(messages=[SystemMsg(name="permission-review", content=system),
                                         UserMsg(name="human-evidence", content=prompt)],
                               tools=None, tool_choice=None)
        # 固定 SDK 把 CancelledError 翻译成空响应；还原取消语义，不能产出迟到结论。
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            raise asyncio.CancelledError
        if not getattr(response, "is_last", False):
            raise ValueError("审核响应没有完整结束")
        reason = str(getattr(response, "finished_reason", ""))
        if reason.endswith(("INTERRUPTED", "LENGTH", "MAX_TOKENS")):
            raise ValueError("审核响应被中断或截断")
        blocks = response.content or []
        # thinking block 丢弃；tool_call 或任何其他结果不能成为许可。
        if any(block.type not in {"text", "thinking"} for block in blocks):
            raise ValueError("审核响应包含非文本行动")
        text = "".join(block.text for block in blocks if block.type == "text")
        usage = getattr(response, "usage", None)
        return ReviewModelResponse(text=text,
                                  input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
                                  output_tokens=int(getattr(usage, "output_tokens", 0) or 0))
