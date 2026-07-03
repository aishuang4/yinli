"""运行后节点记忆持久化测试。"""

from __future__ import annotations

import asyncio

import pytest

from src.memory.node_persist import MemoryNodePersistInput, persist_completed_run_memory_node
from src.server.routes.chat.completion import finalize_successful_leader_run
from src.storage import database


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    """使用临时 SQLite，避免污染开发库。"""

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "agent_workstation.db")
    monkeypatch.setattr(database, "_initialized", False)


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


def test_successful_completion_triggers_memory_persist(isolated_db, monkeypatch):
    calls = []

    def fake_persist(item):
        calls.append(item)
        return {"id": "node-1"}

    monkeypatch.setattr(
        "src.server.routes.chat.completion.persist_completed_run_memory_node",
        fake_persist,
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
                team_activities=[],
                team_id=None,
                team_name=None,
            ),
        ),
    )

    assert any("RUN_COMPLETED" in event for event in events)
    assert calls[0].user_message == "用户输入"
    assert calls[0].assistant_text == "最终回答"


def test_successful_completion_ignores_memory_persist_failure(isolated_db, monkeypatch):
    def fake_persist(item):
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "src.server.routes.chat.completion.persist_completed_run_memory_node",
        fake_persist,
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
                team_activities=[],
                team_id=None,
                team_name=None,
            ),
        ),
    )

    assert any("RUN_COMPLETED" in event for event in events)


async def _collect_async(generator):
    return [item async for item in generator]


class FakeMemoryNodeService:
    def __init__(self):
        self.items = []

    def upsert_node(self, item):
        self.items.append(item)
        return {"id": "node-1"}
