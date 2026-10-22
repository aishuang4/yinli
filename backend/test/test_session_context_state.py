"""会话级上下文状态落库与实例重建恢复。"""
from __future__ import annotations

from src.storage.records.session_context_state import delete_session_context_state
from src.storage.records.session_context_state import load_session_context_state
from src.storage.records.session_context_state import save_session_context_state


from types import SimpleNamespace

import pytest
from agentscope.state import AgentState

from src.agents.session_context import restore_session_context_state, snapshot_session_context_state
from src.storage.engine import database


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    yield


def _agent() -> SimpleNamespace:
    return SimpleNamespace(state=AgentState(), name="Agent")


def test_save_and_load_round_trip_drops_transient_keys(isolated_db) -> None:
    agent = _agent()
    agent.state.summary = "<system-info>压缩摘要</system-info>"
    agent.state.middle_context["aw_activated_skills"] = {"word-documents": {"markdown": "# SKILL", "activatedAt": 1.0}}
    agent.state.middle_context["aw_activated_skills_pending_check"] = True
    agent.state.middle_context["aw_context_compression"] = {"count": 2, "pending": []}
    agent.state.middle_context["not_serializable"] = object()

    summary, middle = snapshot_session_context_state(agent)
    save_session_context_state(session_id="s1", agent_id="a1", summary=summary, middle_context=middle)

    record = load_session_context_state("s1")
    assert record is not None
    assert record["agentId"] == "a1"
    assert record["summary"] == "<system-info>压缩摘要</system-info>"
    assert set(record["middleContext"]) == {"aw_activated_skills", "aw_context_compression"}

    # 覆盖写入而不是新增行。
    save_session_context_state(session_id="s1", agent_id="a1", summary="", middle_context={})
    record = load_session_context_state("s1")
    assert record["summary"] == ""
    assert record["middleContext"] == {}

    delete_session_context_state("s1")
    assert load_session_context_state("s1") is None
    assert load_session_context_state("") is None


def test_restore_does_not_override_live_state() -> None:
    agent = _agent()
    assert restore_session_context_state(agent, "旧摘要", {"aw_activated_skills": {"x": {}}}) is True
    assert agent.state.summary == "旧摘要"
    assert agent.state.middle_context["aw_activated_skills"] == {"x": {}}

    # 实例已有摘要 / 同名 key 时不覆盖。
    agent.state.middle_context["aw_activated_skills"] = {"live": {}}
    assert restore_session_context_state(agent, "更旧的摘要", {"aw_activated_skills": {"stale": {}}}) is False
    assert agent.state.summary == "旧摘要"
    assert agent.state.middle_context["aw_activated_skills"] == {"live": {}}


def test_snapshot_flattens_block_summary() -> None:
    from agentscope.message import TextBlock

    agent = _agent()
    agent.state.summary = [TextBlock(type="text", text="A"), TextBlock(type="text", text="B")]
    summary, _ = snapshot_session_context_state(agent)
    assert summary == "AB"
