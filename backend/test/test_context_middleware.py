"""上下文治理 middleware：一级无模型整理与压缩事件记录。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from agentscope.message import (
    AssistantMsg,
    Base64Source,
    DataBlock,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
    UserMsg,
)
from agentscope.state import AgentState

from src.server.routes.chat.context_middleware import (
    COMPRESSION_KEY,
    ContextCompressionMiddleware,
    ContextHygieneMiddleware,
    compact_stale_context,
    context_governance_stats,
    drain_compression_records,
    set_context_session_id,
)


def _tool_turn(user_text: str, tool_output: str, *, tool_name: str = "Read") -> list:
    # AgentScope 2.0 里 tool_call 与 tool_result 都在 assistant 消息中。
    return [
        UserMsg(name="user", content=user_text),
        AssistantMsg(name="Agent", content=[ToolCallBlock(id=f"c-{user_text}", name=tool_name, input="{}")]),
        AssistantMsg(name="Agent", content=[ToolResultBlock(id=f"c-{user_text}", name=tool_name, output=tool_output)]),
        AssistantMsg(name="Agent", content="好的"),
    ]


def _agent(context: list) -> SimpleNamespace:
    state = AgentState()
    state.context = context
    return SimpleNamespace(state=state, name="Agent")


def test_compact_keeps_recent_turns_and_stubs_older_large_tool_results() -> None:
    big = "行内容\n" * 400
    context = _tool_turn("第一轮", big) + _tool_turn("第二轮", big) + _tool_turn("第三轮", big)
    agent = _agent(context)

    result = compact_stale_context(agent)

    assert result["stubbedResults"] == 1
    assert result["savedChars"] > 0
    first_result = agent.state.context[2].content[0]
    assert isinstance(first_result, ToolResultBlock)
    assert "[上下文已整理]" in first_result.output
    assert "Read" in first_result.output
    # 最近两轮原样保留。
    assert agent.state.context[6].content[0].output == big
    assert agent.state.context[10].content[0].output == big
    # tool_call / tool_result 配对没有被破坏。
    assert agent.state.context[1].content[0].id == agent.state.context[2].content[0].id
    stats = context_governance_stats(agent)
    assert stats["stubbedToolResults"] == 1


def test_compact_skips_small_outputs_and_skill_bodies() -> None:
    small = "短输出"
    skill_body = "# SKILL\n" + "说明\n" * 400
    context = (
        _tool_turn("一", small)
        + _tool_turn("二", skill_body, tool_name="Skill")
        + _tool_turn("三", small)
        + _tool_turn("四", small)
    )
    agent = _agent(context)

    result = compact_stale_context(agent)

    assert result["stubbedResults"] == 0
    assert agent.state.context[6].content[0].output == skill_body


def test_compact_replaces_old_images_with_text_placeholder() -> None:
    image = DataBlock(
        type="data",
        source=Base64Source(type="base64", media_type="image/png", data="aGVsbG8="),
    )
    context = [
        UserMsg(name="user", content=[TextBlock(type="text", text="看这张图"), image]),
        AssistantMsg(name="Agent", content="看到了"),
        UserMsg(name="user", content="第二轮"),
        AssistantMsg(name="Agent", content="好"),
        UserMsg(name="user", content="第三轮"),
        AssistantMsg(name="Agent", content="好"),
    ]
    agent = _agent(context)

    result = compact_stale_context(agent)

    assert result["droppedImages"] == 1
    blocks = agent.state.context[0].content
    assert all(isinstance(block, TextBlock) for block in blocks)
    assert "图片已移出上下文" in blocks[1].text


def test_hygiene_middleware_runs_before_reply() -> None:
    big = "x" * 2000
    agent = _agent(_tool_turn("一", big) + _tool_turn("二", big) + _tool_turn("三", big))
    seen: list[str] = []

    async def next_handler(**_kwargs):
        seen.append("reply")
        yield "event"

    async def run():
        items = []
        async for item in ContextHygieneMiddleware().on_reply(agent, {}, next_handler):
            items.append(item)
        return items

    assert asyncio.run(run()) == ["event"]
    assert seen == ["reply"]
    assert "[上下文已整理]" in agent.state.context[2].content[0].output


def test_compression_middleware_records_only_real_compressions() -> None:
    agent = _agent([UserMsg(name="user", content="a"), AssistantMsg(name="Agent", content="b")])

    async def noop(**_kwargs):
        return None

    async def compress(**_kwargs):
        agent.state.summary = "<system-info>摘要</system-info>"
        agent.state.context = agent.state.context[-1:]

    asyncio.run(ContextCompressionMiddleware().on_compress_context(agent, {}, noop))
    assert drain_compression_records(agent) == []
    assert context_governance_stats(agent) == {}

    asyncio.run(ContextCompressionMiddleware().on_compress_context(agent, {}, compress))
    records = drain_compression_records(agent)
    assert len(records) == 1
    assert records[0]["index"] == 1
    assert records[0]["messagesBefore"] == 2
    assert records[0]["messagesAfter"] == 1
    assert records[0]["summaryCharsAfter"] == len("<system-info>摘要</system-info>")
    # 取走后不再重复；累计次数仍在。
    assert drain_compression_records(agent) == []
    assert context_governance_stats(agent)["compressionCount"] == 1
    assert agent.state.middle_context[COMPRESSION_KEY]["pending"] == []


def test_set_context_session_id_updates_agent_state() -> None:
    agent = _agent([])
    set_context_session_id(agent, "session-1")
    assert agent.state.session_id == "session-1"
    set_context_session_id(agent, "")
    assert agent.state.session_id == "session-1"
