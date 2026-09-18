"""generation Observation 的上下文快照。"""

from types import SimpleNamespace

from src.server.routes.chat.generation_snapshot import (
    merge_generation_output,
    parse_tool_arguments,
    snapshot_model_input,
    tool_output_text,
)


def test_snapshot_model_input_keeps_system_and_messages() -> None:
    agent = SimpleNamespace(
        _system_prompt="你是助手。\n今天处理文档。",
        model=SimpleNamespace(model_name="deepseek-v4-flash"),
        state=SimpleNamespace(
            summary="上一轮已读 README",
            context=[
                SimpleNamespace(role="user", name="barbara", content="帮我看看今天武汉天气怎么样"),
                SimpleNamespace(
                    role="assistant",
                    name="assistant",
                    content=[
                        SimpleNamespace(type="thinking", thinking="先查天气"),
                        SimpleNamespace(type="tool_call", id="c1", name="Bash", input='{"command":"curl wttr.in"}'),
                    ],
                ),
            ],
        ),
    )

    snapshot = snapshot_model_input(
        agent,
        extra_message=SimpleNamespace(role="user", name="barbara", content="补一条当前用户消息"),
    )
    assert snapshot["model"] == "deepseek-v4-flash"
    assert "处理文档" in snapshot["system"]
    assert snapshot["summary"] == "上一轮已读 README"
    assert snapshot["messages"][0]["text"] == "帮我看看今天武汉天气怎么样"
    assert snapshot["messages"][1]["blocks"][1]["input"]["command"] == "curl wttr.in"
    assert snapshot["messages"][-1]["text"] == "补一条当前用户消息"


def test_snapshot_flattens_hint_block_into_user_message() -> None:
    agent = SimpleNamespace(
        _system_prompt="你是助手。",
        model=SimpleNamespace(model_name="deepseek-v4-flash"),
        state=SimpleNamespace(
            summary="",
            context=[
                SimpleNamespace(
                    role="assistant",
                    name="assistant",
                    content=[
                        SimpleNamespace(
                            type="hint",
                            hint="<system-reminder>\n<current-time>2026-09-18 11:07</current-time>\n<timezone>Asia/Shanghai</timezone>\n</system-reminder>",
                        ),
                    ],
                ),
                SimpleNamespace(role="user", name="barbara", content="今天武汉天气"),
            ],
        ),
    )

    snapshot = snapshot_model_input(agent)
    assert snapshot["messages"][0]["role"] == "user"
    assert "<current-time>2026-09-18 11:07</current-time>" in snapshot["messages"][0]["text"]
    assert snapshot["messages"][0].get("blocks") is None
    assert snapshot["messages"][1]["text"] == "今天武汉天气"


def test_merge_generation_output_does_not_wipe_existing_text() -> None:
    merged = merge_generation_output(
        {"text": "已有正文", "thinking": "推理", "toolCalls": [{"name": "Read"}], "usage": {"inputTokens": 3}},
        {"text": "", "thinking": "", "toolCalls": [], "usage": {"outputTokens": 2}, "finishedReason": "completed"},
    )
    assert merged["text"] == "已有正文"
    assert merged["thinking"] == "推理"
    assert merged["toolCalls"][0]["name"] == "Read"
    assert merged["usage"] == {"inputTokens": 3, "outputTokens": 2}


def test_tool_output_text_reads_blocks_and_dicts() -> None:
    assert tool_output_text([{"type": "text", "text": "Wuhan: 24C"}]) == "Wuhan: 24C"
    assert tool_output_text({"text": "ok"}) == "ok"
    assert tool_output_text("") == ""


def test_parse_tool_arguments_accepts_json_string() -> None:
    assert parse_tool_arguments('{"path":"README.md"}') == {"path": "README.md"}
    assert parse_tool_arguments({"path": "a.md"}) == {"path": "a.md"}
