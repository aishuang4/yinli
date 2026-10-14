"""非 user 消息不能带 name，否则部分上游会直接拒绝。"""

from __future__ import annotations

import asyncio

from agentscope.message import AssistantMsg, UserMsg

from src.agents.openai_message_formatter import UserNameOnlyOpenAIFormatter


def test_only_user_messages_keep_name():
    formatter = UserNameOnlyOpenAIFormatter()

    async def run():
        return await formatter.format([
            UserMsg(name="甲", content="你好"),
            AssistantMsg(name="Assistant", content="收到"),
        ])

    messages = asyncio.run(run())
    by_role = {item["role"]: item for item in messages}
    assert by_role["user"]["name"] == "甲"
    assert "name" not in by_role["assistant"]
