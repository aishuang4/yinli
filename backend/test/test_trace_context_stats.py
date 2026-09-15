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
    assert stats["estimatedTokens"] == 10
    assert stats["compressed"] == 0
    assert stats["overTrigger"] == 0

    state.summary = "压缩摘要"
    stats = build_model_call_context_stats(agent)
    assert stats["compressed"] == 1
    assert stats["summaryChars"] == 4

    agent._system_prompt = "x" * 80_000
    stats = build_model_call_context_stats(agent)
    assert stats["estimatedTokens"] >= stats["triggerTokens"]
    assert stats["overTrigger"] == 1

    apply_model_call_token_usage(stats, {"input_tokens": 100, "output_tokens": 12})
    assert stats["inputTokens"] == 100
    assert stats["outputTokens"] == 12
    assert stats["overTrigger"] == 0


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
