"""把用户在 ReAct 中途补充的消息注入当前 Agent。"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from agentscope.message import UserMsg
from agentscope.middleware import MiddlewareBase

from src.server.routes.chat.background_runs import current_chat_background_run_id, take_run_steers


class RunSteerMiddleware(MiddlewareBase):
    """消费 pending_steers，写入 memory 并追加到 system prompt。"""

    def _consume(self, agent: object) -> list[str]:
        notes = take_run_steers(current_chat_background_run_id())
        if not notes:
            return []
        text = "用户在本轮执行中补充了以下信息，请立刻纳入后续推理与工具调用：\n" + "\n".join(
            f"- {item}" for item in notes
        )
        memory = getattr(agent, "memory", None)
        add = getattr(memory, "add", None) if memory is not None else None
        if callable(add):
            try:
                add(UserMsg(content=text))
            except Exception:
                pass
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
        self._consume(agent)
        async for item in next_handler(**input_kwargs):
            yield item
