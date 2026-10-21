"""运行后节点记忆持久化测试。"""

from __future__ import annotations

import asyncio
import time

import pytest

from src.memory.node_persist import MemoryNodePersistInput, persist_completed_run_memory_node
from src.server.routes.chat.completion import finalize_successful_leader_run
from src.server.routes.chat.models import ChatToolRun
from src.server.routes.chat.store import _append_assistant_message, _append_user_message
from src.storage import database


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    """使用临时 SQLite，避免污染开发库。"""



def test_persist_completed_run_memory_node_uses_only_user_and_final_output():
    service = FakeMemoryNodeService()

    node = persist_completed_run_memory_node(
        MemoryNodePersistInput(
            session_id="session-1",
            agent_id="agent-1",
            agent_name="Agent",
            user_message="继续推进 memory hook，不要保存工具过程",
            assistant_text="已经实现 MemoryNodePersistHook。",
        ),
        service=service,
    )

    written = service.items[0]
    assert node == {"id": "node-1"}
    assert written.user_inputs == ["继续推进 memory hook，不要保存工具过程"]
    assert written.agent_final_outputs == ["已经实现 MemoryNodePersistHook。"]
    assert "工具过程" in written.summary
    assert "observation" not in written.summary.lower()
    assert {"memory", "hooks"}.issubset(set(written.tags))


def test_persist_completed_run_memory_node_skips_empty_final_output():
    service = FakeMemoryNodeService()

    node = persist_completed_run_memory_node(
        MemoryNodePersistInput(
            session_id="session-1",
            agent_id="agent-1",
            agent_name="Agent",
            user_message="hello",
            assistant_text="",
        ),
        service=service,
    )

    assert node is None
    assert service.items == []


def test_persist_completed_run_memory_node_skips_trivial_turn():
    service = FakeMemoryNodeService()

    node = persist_completed_run_memory_node(
        MemoryNodePersistInput(
            session_id="session-1",
            agent_id="agent-1",
            agent_name="Agent",
            user_message="谢谢",
            assistant_text="好的",
        ),
        service=service,
    )

    assert node is None
    assert service.items == []


def test_persist_completed_run_memory_node_skips_sensitive_turn():
    service = FakeMemoryNodeService()

    node = persist_completed_run_memory_node(
        MemoryNodePersistInput(
            session_id="session-1",
            agent_id="agent-1",
            agent_name="Agent",
            user_message="记住 token=secret-value-123456",
            assistant_text="不应该保存。",
        ),
        service=service,
    )

    assert node is None
    assert service.items == []


def test_successful_completion_triggers_memory_persist(isolated_db, monkeypatch):
    calls = []

    def fake_professional(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(
        "src.server.routes.chat.completion.schedule_professional_update",
        fake_professional,
    )

    events = asyncio.run(
        _collect_async(
            finalize_successful_leader_run(
                session_id="session-1",
                persist=True,
                agent_id="agent-1",
                agent_name="Agent",
                model_name="model",
                user_message="用户输入",
                assistant_text="最终回答",
                assistant_thinking="thinking should not persist",
                assistant_tool_calls=[],
                assistant_model_caches=[],
                runtime_events=[],
                skill_runs=[],
                active_skill_run=None,
            ),
        ),
    )

    assert any(event.event_type == "RUN_COMPLETED" for event in events)
    assert calls[0]["user_message"] == "用户输入"
    assert calls[0]["assistant_text"] == "最终回答"


def test_successful_completion_records_memory_node_trace_without_chat_event(isolated_db, monkeypatch):
    monkeypatch.setattr(
        "src.server.routes.chat.completion.schedule_professional_update",
        lambda **_kwargs: None,
    )

    events = asyncio.run(
        _collect_async(
            finalize_successful_leader_run(
                session_id="session-1",
                persist=True,
                agent_id="agent-1",
                agent_name="Agent",
                model_name="model",
                user_message="用户输入",
                assistant_text="最终回答",
                assistant_thinking="",
                assistant_tool_calls=[],
                assistant_model_caches=[],
                runtime_events=[],
                skill_runs=[],
                active_skill_run=None,
            ),
        ),
    )
    trace_events = database.list_run_events("session-1")
    memory_events = [event for event in trace_events if event["eventType"] == "MEMORY_AUDIT"]

    assert not any(event.event_type == "MEMORY_AUDIT" for event in events)
    assert not any(event["payload"].get("memoryType") == "memory_node_persist" for event in memory_events)


def test_successful_completion_ignores_memory_persist_failure(isolated_db, monkeypatch):
    monkeypatch.setattr(
        "src.server.routes.chat.completion.schedule_professional_update",
        lambda **_kwargs: None,
    )

    events = asyncio.run(
        _collect_async(
            finalize_successful_leader_run(
                session_id="session-1",
                persist=True,
                agent_id="agent-1",
                agent_name="Agent",
                model_name="model",
                user_message="用户输入",
                assistant_text="最终回答",
                assistant_thinking="",
                assistant_tool_calls=[],
                assistant_model_caches=[],
                runtime_events=[],
                skill_runs=[],
                active_skill_run=None,
            ),
        ),
    )

    assert any(event.event_type == "RUN_COMPLETED" for event in events)


def test_successful_completion_does_not_wait_for_memory_reflection(isolated_db, monkeypatch):
    monkeypatch.setattr(
        "src.server.routes.chat.completion.schedule_professional_update",
        lambda **_kwargs: None,
    )

    started = time.monotonic()
    events = asyncio.run(
        _collect_async(
            finalize_successful_leader_run(
                session_id="session-1",
                persist=True,
                agent_id="agent-1",
                agent_name="Agent",
                model_name="model",
                user_message="以后默认用中文回答。",
                assistant_text="最终回答",
                assistant_thinking="",
                assistant_tool_calls=[],
                assistant_model_caches=[],
                runtime_events=[],
                skill_runs=[],
                active_skill_run=None,
            ),
        ),
    )

    assert any(event.event_type == "RUN_COMPLETED" for event in events)
    assert time.monotonic() - started < 0.15


async def _collect_async(generator):
    return [item async for item in generator]


class FakeMemoryNodeService:
    def __init__(self):
        self.items = []

    def upsert_node(self, item):
        self.items.append(item)
        return {"id": "node-1"}

    def append_turn_to_session_day_node(self, item, **_kwargs):
        return None

    def archive_stale_nodes(self, **_kwargs):
        return []


def test_summary_from_turn_is_one_sentence_without_markdown_noise():
    from src.memory.node_persist import _summary_from_turn

    user = "帮我把 Q3 销售表按地区汇总。顺便看看有没有异常值。"
    assistant = (
        "好的。\n\n## 结果\n\n- **已生成** `q3_by_region.xlsx`，共 5 个地区。\n"
        "- 华东异常偏高，建议复核。\n\n```python\nprint(1)\n```\n" + "详细说明。" * 200
    )

    summary = _summary_from_turn(user, assistant)

    assert summary.startswith("帮我把 Q3 销售表按地区汇总。 → ")
    assert "已生成" in summary
    assert "**" not in summary and "```" not in summary and "##" not in summary
    assert len(summary) <= 170
    assert _summary_from_turn("", "") == "对话记忆"
