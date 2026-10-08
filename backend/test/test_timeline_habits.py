"""时间线检索、习惯跳过和专业区覆盖的行为测试。"""

from __future__ import annotations

import asyncio

import pytest

from types import SimpleNamespace

from agentscope.message import ToolResultBlock

from src.memory.admission import is_trivial_user_message
from src.memory.habit_update import HabitUpdatePayload, apply_habit_update
from src.memory.markdown_store import MarkdownMemoryStore
from src.memory.professional_update import ProfessionalUpdatePayload, apply_professional_update
from src.memory.timeline import search_timeline
from src.server.routes.chat.context_middleware import stub_timeline_retrieve_outputs
from src.server.routes.chat.store import _append_assistant_message, _append_user_message
from src.storage import database
from src.storage.database import DEFAULT_PROJECT_ID, DEFAULT_WORKSPACE_ID
from src.tools.timeline_memory import TimelineIngest, TimelineRetrieve


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "agent_workstation.db")
    monkeypatch.setattr(database, "_initialized", False)
    yield
    database._initialized = False


def test_ordinary_question_skips_habit_model(tmp_path, monkeypatch):
    called = {"count": 0}

    async def fail_if_called(**kwargs):
        called["count"] += 1
        raise AssertionError("普通问题不应调用习惯模型")

    monkeypatch.setattr("src.memory.habit_update.run_habit_update_agent", fail_if_called)
    store = MarkdownMemoryStore(tmp_path / "memory")
    result = asyncio.run(
        apply_habit_update(
            user_message="今天适合喝咖啡还是茶",
            model_name="model",
            store=store,
            persist_audit=False,
        )
    )
    assert result is None
    assert called["count"] == 0


def test_trivial_user_message_skips_habit_model(isolated_db, tmp_path, monkeypatch):
    called = {"count": 0}

    async def fail_if_called(**kwargs):
        called["count"] += 1
        raise AssertionError("寒暄不应调用习惯模型")

    monkeypatch.setattr("src.memory.habit_update.run_habit_update_agent", fail_if_called)
    store = MarkdownMemoryStore(tmp_path / "memory")
    result = asyncio.run(
        apply_habit_update(
            user_message="好的",
            model_name="model",
            store=store,
            persist_audit=False,
        )
    )
    assert result is None
    assert called["count"] == 0
    assert is_trivial_user_message("好的")
    assert not is_trivial_user_message("我以后默认用中文写代码")


def test_timeline_search_returns_neighbor_messages(isolated_db):
    _append_user_message(
        session_id="session-sales",
        agent_id="agent-1",
        agent_name="销售助手",
        mode="agent",
        model_name="model",
        content="帮我把销售表按地区汇总",
        images=[],
        workspace_id="ws-1",
        project_id="proj-1",
    )
    _append_assistant_message(
        session_id="session-sales",
        agent_id="agent-1",
        agent_name="销售助手",
        model_name="model",
        content="华东还没汇总完。",
        error=None,
    )
    _append_user_message(
        session_id="session-other",
        agent_id="agent-2",
        agent_name="别的助手",
        mode="agent",
        model_name="model",
        content="帮我把销售表按地区汇总",
        images=[],
        workspace_id="ws-1",
        project_id="proj-1",
    )

    found = search_timeline(query_text="销售表", agent_id="agent-1", workspace_id="ws-1", project_id="proj-1")
    contents = [item["content"] for item in found["messages"]]
    assert "帮我把销售表按地区汇总" in contents
    assert "华东还没汇总完。" in contents
    assert all(item["agentId"] == "agent-1" for item in found["messages"])
    assert {item["surface"] for item in found["messages"]} == {"chat"}


def test_timeline_marks_scheduled_job_surface(isolated_db):
    _append_user_message(
        session_id="job-session",
        agent_id="job-agent",
        agent_name="Job",
        mode="agent",
        model_name="model",
        content="夜里跑过一次报表",
        images=[],
    )
    database.ensure_db()
    with database._lock, database._connect() as conn:
        conn.execute("UPDATE chat_sessions SET kind = 'scheduled_job' WHERE id = ?", ("job-session",))

    found = search_timeline(query_text="报表", surface="job")
    assert found["messages"]
    assert found["messages"][0]["surface"] == "job"
    chat_only = search_timeline(query_text="报表", surface="chat")
    assert chat_only["messages"] == []


def test_professional_write_can_shrink(tmp_path):
    store = MarkdownMemoryStore(tmp_path / "memory")
    store.write_professional("agent-1", "第一条做法\n第二条做法\n")
    saved = store.write_professional("agent-1", "只留下第一条做法")
    assert saved.content.strip() == "只留下第一条做法"
    assert "第二条" not in store.read_professional("agent-1").content


def test_timeline_tools_retrieve_then_ingest(isolated_db):
    _append_user_message(
        session_id="session-tool",
        agent_id="agent-1",
        agent_name="助手",
        mode="agent",
        model_name="model",
        content="上次说过用中文写注释",
        images=[],
    )
    retrieve = asyncio.run(
        TimelineRetrieve(audit_session_id="", audit_agent_id="agent-1", audit_agent_name="助手")(
            queryText="中文写注释",
            agentId="agent-1",
        )
    )
    assert retrieve.state.value == "success"
    ingest = asyncio.run(
        TimelineIngest(audit_session_id="", audit_agent_id="agent-1", audit_agent_name="助手")(
            note="这次要沿用中文注释",
            sourceIds=["m1"],
        )
    )
    assert ingest.content[0].text == "这次要沿用中文注释"
    empty = asyncio.run(TimelineIngest()(note="   "))
    assert empty.state.value == "error"


def test_habit_write_replaces_file_and_failure_keeps_old(isolated_db, tmp_path, monkeypatch):
    store = MarkdownMemoryStore(tmp_path / "memory")
    store.write_habits("default", "旧习惯")

    async def write_new(**kwargs):
        return HabitUpdatePayload(should_write=True, content="喜欢喝咖啡", reason="稳定偏好")

    monkeypatch.setattr("src.memory.habit_update.run_habit_update_agent", write_new)
    saved = asyncio.run(
        apply_habit_update(
            user_message="我以后喝咖啡，不喝奶茶",
            model_name="model",
            store=store,
            persist_audit=False,
        )
    )
    assert saved is not None
    assert "咖啡" in store.read_habits("default").content
    assert "旧习惯" not in store.read_habits("default").content

    async def boom(**kwargs):
        raise RuntimeError("model down")

    monkeypatch.setattr("src.memory.habit_update.run_habit_update_agent", boom)
    failed = asyncio.run(
        apply_habit_update(
            user_message="我以后改喝茶",
            model_name="model",
            store=store,
            persist_audit=False,
        )
    )
    assert failed is None
    assert "咖啡" in store.read_habits("default").content


def test_professional_update_can_shrink_full_text(tmp_path, monkeypatch):
    store = MarkdownMemoryStore(tmp_path / "memory")
    store.write_professional("agent-1", "第一条做法\n第二条做法")

    async def shrink(**kwargs):
        return ProfessionalUpdatePayload(should_write=True, content="只留下第一条做法", reason="第二条不再重复")

    monkeypatch.setattr("src.memory.professional_update.run_professional_update_agent", shrink)
    monkeypatch.setattr("src.memory.professional_update.search_timeline", lambda **kwargs: {"messages": []})
    saved = asyncio.run(
        apply_professional_update(
            agent_id="agent-1",
            agent_name="助手",
            user_message="再做一次汇总",
            assistant_text="已按第一条做法完成。",
            model_name="model",
            store=store,
            persist_audit=False,
        )
    )
    assert saved is not None
    assert store.read_professional("agent-1").content.strip() == "只留下第一条做法"


def test_timeline_marks_group_surface(isolated_db):
    database.ensure_db()
    now = "2026-09-28T04:00:00+00:00"
    with database._lock, database._connect() as conn:
        conn.execute(
            """
            INSERT INTO group_studios (
                id, workspace_id, project_id, chat_session_id, title, status,
                current_task_card_id, budget_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("gstu_tl", DEFAULT_WORKSPACE_ID, DEFAULT_PROJECT_ID, "sess-group-tl", "群", "open", "card", "{}", now, now),
        )
        conn.execute(
            """
            INSERT INTO group_messages (
                id, group_id, speaker_id, role, intent, body, created_at, sort_order
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("gmsg_tl", "gstu_tl", "human", "human", "human_directive", "群里说过按地区汇总", now, 1),
        )
    found = search_timeline(query_text="按地区汇总", surface="group")
    assert found["messages"]
    assert found["messages"][0]["surface"] == "group"
    assert search_timeline(query_text="按地区汇总", surface="chat")["messages"] == []


def test_habit_write_is_visible_on_next_injection(tmp_path, monkeypatch):
    store = MarkdownMemoryStore(tmp_path / "memory")

    async def write_new(**kwargs):
        return HabitUpdatePayload(should_write=True, content="喜欢喝咖啡", reason="稳定偏好")

    monkeypatch.setattr("src.memory.habit_update.run_habit_update_agent", write_new)
    asyncio.run(
        apply_habit_update(
            user_message="我以后喝咖啡，不喝奶茶",
            model_name="model",
            store=store,
            persist_audit=False,
        )
    )
    from src.memory.retrieval import build_habit_and_professional_prompt

    text = build_habit_and_professional_prompt(user_id="default", agent_id="", include_professional=False, markdown_store=store)
    assert "喜欢喝咖啡" in text
    assert "<recalled-memory>" not in text
    assert "memory_node_query" not in text


def test_professional_update_queries_only_that_agent(tmp_path, monkeypatch):
    store = MarkdownMemoryStore(tmp_path / "memory")
    seen = {}

    async def keep(**kwargs):
        return ProfessionalUpdatePayload(should_write=False, content="", reason="这次不用改")

    def fake_search(**kwargs):
        seen.update(kwargs)
        return {"messages": [{"agentId": kwargs.get("agent_id"), "role": "user", "content": "只属于这个智能体", "createdAt": "2026-09-28"}]}

    monkeypatch.setattr("src.memory.professional_update.run_professional_update_agent", keep)
    monkeypatch.setattr("src.memory.professional_update.search_timeline", fake_search)
    saved = asyncio.run(
        apply_professional_update(
            agent_id="agent-9",
            agent_name="助手",
            user_message="再做一次同样的汇总",
            assistant_text="已完成。",
            model_name="model",
            store=store,
            persist_audit=False,
        )
    )
    assert saved is None
    assert seen["agent_id"] == "agent-9"
    assert store.read_professional("agent-9").content.strip() == ""


def test_empty_timeline_filter_is_capped(isolated_db):
    for index in range(5):
        _append_user_message(
            session_id=f"session-cap-{index}",
            agent_id="agent-1",
            agent_name="助手",
            mode="agent",
            model_name="model",
            content=f"第 {index} 条独立对话",
            images=[],
        )
    found = search_timeline(limit=2)
    assert found["hitCount"] <= 2


def test_ingest_stubs_retrieve_output():
    block = ToolResultBlock(name="timeline_retrieve", id="call-1", output="这是一段很长的检索原文")
    agent = SimpleNamespace(state=SimpleNamespace(context=[SimpleNamespace(content=[block])]))
    assert stub_timeline_retrieve_outputs(agent) == 1
    assert "检索原文" not in str(block.output)
    assert "已移出上下文" in str(block.output)
