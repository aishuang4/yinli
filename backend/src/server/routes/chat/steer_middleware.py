"""把用户在 ReAct 中途补充的消息注入当前 Agent。"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from agentscope.message import UserMsg
from agentscope.middleware import MiddlewareBase

from src.server.routes.chat.background_runs import current_chat_background_run_id, take_run_steers

STEER_PREFIX = "用户在本轮执行中补充了以下信息，请立刻纳入后续推理与工具调用："


def _append_steer_to_context(agent: object, text: str) -> bool:
    """把补充消息作为 user 消息写入 AgentScope `state.context`。

    AgentScope 2.0.x 的 Agent 没有 `memory` 属性，会话历史只在 `state.context`；
    写进 context 后补充内容会随对话一直保留，而不是只出现在某一次模型调用里。
    """
    state = getattr(agent, "state", None)
    context = getattr(state, "context", None)
    if not isinstance(context, list):
        return False
    context.append(UserMsg(name="user", content=text))
    return True


class RunSteerMiddleware(MiddlewareBase):
    """消费 pending_steers，写入 AgentScope 对话上下文；写不进去时退回追加到 system prompt。"""

    def _consume(self, agent: object) -> list[str]:
        notes = take_run_steers(current_chat_background_run_id())
        if not notes:
            return []
        text = STEER_PREFIX + "\n" + "\n".join(f"- {item}" for item in notes)
        if _append_steer_to_context(agent, text):
            return []
        return notes

    async def on_system_prompt(self, agent: object, current_prompt: str) -> str:
        notes = self._consume(agent)
        if not notes:
            return current_prompt or ""
        extra = "\n".join(f"- {item}" for item in notes)
        return (current_prompt or "") + "\n\n# 用户补充\n" + extra + "\n"

    async def on_acting(
        self,
        agent: object,
        input_kwargs: dict[str, Any],
        next_handler: object,
    ) -> AsyncGenerator[object, None]:
        # 刻意不在这里消费：acting 期间 context 末尾是尚未收到结果的 tool call，
        # 此时插入 user 消息会打断 assistant(tool_calls) -> tool 的相邻顺序，
        # 部分供应商会直接拒绝请求。补充消息统一在下一次 reasoning 前
        # （on_system_prompt）写入，工具结果已经落到 context 之后。
        async for item in next_handler(**input_kwargs):
            yield item
