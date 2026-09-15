"""Trace 可观测性补充：模型调用上下文规模与工具输入预览。"""

from __future__ import annotations

from types import SimpleNamespace

from agentscope.message import AssistantMsg, ToolCallBlock, ToolResultBlock, UserMsg
from agentscope.state import AgentState

from src.server.routes.chat.models import ChatToolRun
from src.server.routes.chat.trace_recorder import (
    TOOL_INPUT_PREVIEW_LIMIT,
    _tool_input_preview,
    apply_model_call_token_usage,
    build_model_call_context_stats,
)


def test_model_call_context_stats_counts_prompt_summary_and_context():
    state = AgentState()
    state.summary = "摘要" * 10
    state.context = [
        UserMsg(name="user", content="你好"),
        AssistantMsg(
            name="Agent",
            content=[
                ToolCallBlock(id="c1", name="Read", input='{"file_path":"a.md"}'),
                ToolResultBlock(id="c1", name="Read", output="内容" * 5),
            ],
        ),
    ]
    agent = SimpleNamespace(
        _system_prompt="p" * 100,
        state=state,
        toolkit=SimpleNamespace(tool_groups=[SimpleNamespace(tools=[object(), object()])]),
    )

    stats = build_model_call_context_stats(agent)

    assert stats["systemPromptChars"] == 100
    assert stats["summaryChars"] == 20
    assert stats["contextMessages"] == 2
    assert stats["toolResultBlocks"] == 1
    assert stats["toolCount"] == 2
    assert stats["contextChars"] == len("你好") + len('{"file_path":"a.md"}') + len("内容" * 5)


def test_model_call_context_stats_reports_window_and_compression_threshold():
    state = AgentState()
    agent = SimpleNamespace(
        _system_prompt="p" * 20,
        state=state,
        model=SimpleNamespace(context_size=32_768),
        context_config=SimpleNamespace(trigger_ratio=0.8),
    )

    stats = build_model_call_context_stats(agent)

    assert stats["contextWindow"] == 32_768
    assert stats["triggerRatio"] == 0.8
    assert stats["triggerTokens"] == 26_214
    # 与 AgentScope count_tokens 同源：utf-8 字节 / 4，20 个 ASCII 字符 = 5 tokens。
    assert stats["estimatedTokens"] == 5
    assert stats["estimateMethod"] == "bytes/4"
    assert stats["compressed"] == 0
    assert stats["overTrigger"] == 0

    state.summary = "压缩摘要"
    stats = build_model_call_context_stats(agent)
    assert stats["compressed"] == 1
    assert stats["summaryChars"] == 4
    # 4 个中文 = 12 字节 → 3 tokens，加 system prompt 5 tokens。
    assert stats["estimatedTokens"] == 8

    agent._system_prompt = "x" * 120_000
    stats = build_model_call_context_stats(agent)
    assert stats["estimatedTokens"] >= stats["triggerTokens"]
    assert stats["overTrigger"] == 1

    apply_model_call_token_usage(stats, {"input_tokens": 100, "output_tokens": 12})
    assert stats["inputTokens"] == 100
    assert stats["outputTokens"] == 12
    assert stats["overTrigger"] == 0
    assert stats["estimateErrorPct"] > 0


def test_model_call_context_stats_includes_prompt_sections_and_governance():
    from src.server.routes.chat.context_middleware import COMPRESSION_KEY, HYGIENE_KEY
    from src.server.routes.chat.trace_recorder import PROMPT_SECTIONS_ATTR, TOOL_SCHEMA_BYTES_ATTR

    state = AgentState()
    state.middle_context[COMPRESSION_KEY] = {"count": 2, "pending": []}
    state.middle_context[HYGIENE_KEY] = {"stubbedResults": 3, "droppedImages": 1, "savedChars": 4200}
    agent = SimpleNamespace(_system_prompt="p" * 40, state=state)
    setattr(agent, PROMPT_SECTIONS_ATTR, {"agent": 30, "memory": 10})
    setattr(agent, TOOL_SCHEMA_BYTES_ATTR, 400)

    stats = build_model_call_context_stats(agent)

    assert stats["promptSections"] == {"agent": 30, "memory": 10}
    assert stats["toolSchemaChars"] == 400
    # (40 + 400) / 4 = 110
    assert stats["estimatedTokens"] == 110
    assert stats["compressionCount"] == 2
    assert stats["stubbedToolResults"] == 3
    assert stats["droppedImages"] == 1
    assert stats["hygieneSavedChars"] == 4200


def test_model_call_context_stats_async_measures_real_tool_schemas():
    import asyncio

    from src.server.routes.chat.trace_recorder import TOOL_SCHEMA_BYTES_ATTR, build_model_call_context_stats_async

    async def fake_schemas(groups):
        assert groups == ["extra"]
        return [{"type": "function", "function": {"name": "Read", "parameters": {}}}]

    state = AgentState()
    state.tool_context.activated_groups = ["extra"]
    agent = SimpleNamespace(
        _system_prompt="",
        state=state,
        toolkit=SimpleNamespace(get_tool_schemas=fake_schemas, tool_groups=[]),
    )

    stats = asyncio.run(build_model_call_context_stats_async(agent))

    assert stats["toolCount"] == 1
    assert stats["toolSchemaChars"] > 0
    assert getattr(agent, TOOL_SCHEMA_BYTES_ATTR) == stats["toolSchemaChars"]
    # 同步版复用缓存的 schema 字节数。
    assert build_model_call_context_stats(agent)["toolSchemaChars"] == stats["toolSchemaChars"]


def test_tool_input_preview_redacts_and_trims():
    calls = [
        ChatToolRun(
            id="c1",
            name="HttpRequest",
            args='{"url":"https://x.test","headers":{"Authorization":"Bearer sk-1234567890abcdefghijklmnop"}}' + "x" * 2000,
        ),
    ]

    preview = _tool_input_preview(calls, "c1")

    assert "sk-1234567890abcdefghijklmnop" not in preview
    assert len(preview) <= TOOL_INPUT_PREVIEW_LIMIT + 3
    assert _tool_input_preview(calls, "missing") == ""
    assert _tool_input_preview(None, "c1") == ""
