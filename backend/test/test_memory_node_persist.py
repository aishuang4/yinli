"""运行后节点记忆持久化测试。"""

from __future__ import annotations

import asyncio
import time

import pytest

from src.memory.node_persist import MemoryNodePersistInput, persist_completed_run_memory_node
from src.memory.reflection import MemoryReflectionInput, MemoryReflectionResult
from src.server.routes.chat.completion import (
    _build_memory_reflection_input,
    _run_memory_reflection_background,
    finalize_successful_leader_run,
)
from src.server.routes.chat.models import ChatToolRun
from src.server.routes.chat.store import _append_assistant_message, _append_user_message
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
            ),
        ),
    )

    assert any(event.event_type == "RUN_COMPLETED" for event in events)
    assert calls[0].user_message == "用户输入"
    assert calls[0].assistant_text == "最终回答"


def test_successful_completion_records_memory_node_trace_without_chat_event(isolated_db, monkeypatch):
    def fake_persist(item):
        return {
            "id": "node-1",
            "title": "节点记忆",
            "node_date": "2026-07-06",
            "vector_status": "synced",
        }

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
            ),
        ),
    )
    trace_events = database.list_run_events("session-1")
    memory_events = [event for event in trace_events if event["eventType"] == "MEMORY_AUDIT"]

    assert not any(event.event_type == "MEMORY_AUDIT" for event in events)
    assert memory_events
    assert memory_events[0]["scope"] == "memory"
    assert memory_events[0]["payload"]["type"] == "memory"
    assert memory_events[0]["payload"]["memoryType"] == "memory_node_persist"
    assert memory_events[0]["payload"]["nodeId"] == "node-1"


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
            ),
        ),
    )

    assert any(event.event_type == "RUN_COMPLETED" for event in events)


def test_successful_completion_does_not_wait_for_memory_reflection(isolated_db, monkeypatch):
    async def slow_reflection(*args, **kwargs):
        await asyncio.sleep(0.2)

    monkeypatch.setattr(
        "src.server.routes.chat.completion.reflect_completed_run_memory_with_agents",
        slow_reflection,
    )
    monkeypatch.setattr(
        "src.server.routes.chat.completion.persist_completed_run_memory_node",
        lambda item: {"id": "node-1"},
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


def test_memory_reflection_background_records_memory_trace(isolated_db, monkeypatch):
    async def fake_reflection(*args, **kwargs):
        return MemoryReflectionResult(
            skill_evolution_record={
                "id": "skill-evolution-1",
                "status": "registered",
                "registeredSkillId": "custom-memory-hooks",
            },
        )

    monkeypatch.setattr(
        "src.server.routes.chat.completion.reflect_completed_run_memory_with_agents",
        fake_reflection,
    )

    asyncio.run(
        _run_memory_reflection_background(
            MemoryReflectionInput(
                session_id="session-1",
                agent_id="agent-1",
                agent_name="Agent",
                user_message="帮我做一个可复用 Skill。",
                assistant_text="已完成。",
                source_run_id="run-1",
            ),
            model_name="model",
            persist=True,
        ),
    )
    memory_events = [
        event
        for event in database.list_run_events("session-1")
        if event["eventType"] == "MEMORY_AUDIT"
    ]

    assert len(memory_events) == 1
    assert memory_events[0]["payload"]["type"] == "memory"
    assert memory_events[0]["payload"]["memoryType"] == "memory_reflection"
    assert memory_events[0]["payload"]["skillEvolutionStatus"] == "registered"
    assert memory_events[0]["payload"]["registeredSkillId"] == "custom-memory-hooks"


def test_successful_completion_emits_skill_generating_notice_only(isolated_db, monkeypatch):
    monkeypatch.setattr(
        "src.server.routes.chat.completion.persist_completed_run_memory_node",
        lambda item: {"id": "node-1"},
    )

    events = asyncio.run(
        _collect_async(
            finalize_successful_leader_run(
                session_id="session-1",
                persist=True,
                agent_id="agent-1",
                agent_name="Agent",
                model_name="model",
                user_message="帮我做一个可复用的 Skill 流程。",
                assistant_text="已完成实现，并通过测试验证。",
                assistant_thinking="",
                assistant_tool_calls=[
                    ChatToolRun(
                        id="call-1",
                        name="Edit",
                        args='{"file_path":"backend/src/memory/reflection.py"}',
                        result="updated",
                        status="completed",
                    ),
                ],
                assistant_model_caches=[],
                runtime_events=[],
                skill_runs=[],
                active_skill_run=None,
            ),
        ),
    )

    notice_events = [event for event in events if event.event_type == "MEMORY_SKILL_GENERATING"]
    assert len(notice_events) == 1
    assert notice_events[0].data["payload"]["message"] == "Skills 生成中"
    assert "registeredSkillId" not in notice_events[0].data["payload"]
    assert "draftSkillContent" not in notice_events[0].data["payload"]


def test_manual_skill_request_uses_previous_completed_turn(isolated_db):
    _append_user_message(
        session_id="session-1",
        agent_id="agent-1",
        agent_name="Agent",
        mode="agent",
        model_name="model",
        content="帮我整理一个 API 文档检查流程。",
        images=[],
    )
    _append_assistant_message(
        session_id="session-1",
        agent_id="agent-1",
        agent_name="Agent",
        model_name="model",
        content="已完成 API 文档检查流程：检查参数、错误码和示例。",
        error=None,
    )
    _append_user_message(
        session_id="session-1",
        agent_id="agent-1",
        agent_name="Agent",
        mode="agent",
        model_name="model",
        content="把这个生成 Skill",
        images=[],
    )

    item = _build_memory_reflection_input(
        session_id="session-1",
        agent_id="agent-1",
        agent_name="Agent",
        user_message="把这个生成 Skill",
        assistant_text="好的，Skills 生成中。",
        source_run_id="run-manual",
        tool_calls=[],
        runtime_events=[],
    )

    assert item.manual_skill_request is True
    assert item.user_message == "帮我整理一个 API 文档检查流程。"
    assert item.assistant_text.startswith("已完成 API 文档检查流程")
    assert item.source_message_id


async def _collect_async(generator):
    return [item async for item in generator]


class FakeMemoryNodeService:
    def __init__(self):
        self.items = []

    def upsert_node(self, item):
        self.items.append(item)
        return {"id": "node-1"}
