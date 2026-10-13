"""送往 OpenAI 兼容接口前，去掉非 user 消息上的 name。

部分上游只允许 role=user 携带 name，assistant / tool / system 带上就会 400。
"""

from __future__ import annotations

from typing import Any

from agentscope.formatter import MoonshotChatFormatter, OpenAIChatFormatter


def strip_non_user_names(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """就地删除非 user 消息的 name。user 消息保留，用来区分发言人。"""

    for message in messages:
        if message.get("role") != "user":
            message.pop("name", None)
    return messages


class UserNameOnlyOpenAIFormatter(OpenAIChatFormatter):
    """OpenAI Chat formatter：只有 user 消息保留 name。"""

    async def format(self, msgs: list) -> list[dict[str, Any]]:
        return strip_non_user_names(await super().format(msgs))


class UserNameOnlyMoonshotFormatter(MoonshotChatFormatter):
    """Moonshot formatter：同样只让 user 消息保留 name。"""

    async def format(self, msgs: list) -> list[dict[str, Any]]:
        return strip_non_user_names(await super().format(msgs))
