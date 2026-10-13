"""各 Runtime 共用治理组件。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from agentscope.state import AgentState

from src.server.routes.chat import runtime_governance as governance
from src.server.routes.chat.context_middleware import COMPRESSION_KEY
from src.storage import database


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    yield
    database.forget_db(database.db_path())


def test_middleware_chain_shape() -> None:
    with_skill = [type(item).__name__ for item in governance.build_context_governance_middlewares(skill_context=True)]
    without = [type(item).__name__ for item in governance.build_context_governance_middlewares(skill_context=False)]
    assert with_skill == [
        "SkillContextMiddleware",
        "TimelineRecallMiddleware",
        "ContextHygieneMiddleware",
        "ContextCompressionMiddleware",
        "ToolLoopGuardMiddleware",
    ]
    assert without == [
        "TimelineRecallMiddleware",
        "ContextHygieneMiddleware",
        "ContextCompressionMiddleware",
        "ToolLoopGuardMiddleware",
    ]


def test_prepare_restore_persist_round_trip(isolated_db) -> None:
    agent = SimpleNamespace(state=AgentState(), name="A")
    governance.prepare_agent_governance(agent, session_id="studio-1:member-1", prompt_sections={"team": 10})
    assert agent.state.session_id == "studio-1:member-1"
    agent.state.summary = "摘要"
    agent.state.middle_context["aw_activated_skills"] = {"x": {"markdown": "# S", "activatedAt": 1.0}}
    governance.persist_agent_session_state(agent, session_id="studio-1:member-1", agent_id="agent-1")

    fresh = SimpleNamespace(state=AgentState(), name="A")
    assert governance.restore_agent_session_state(fresh, "studio-1:member-1") is True
    assert fresh.state.summary == "摘要"
    assert "aw_activated_skills" in fresh.state.middle_context
    assert governance.restore_agent_session_state(fresh, "") is False


def test_observe_model_call_writes_context_usage_and_compression_traces(isolated_db, monkeypatch) -> None:
    recorded: list[dict] = []

    def fake_record(**kwargs):
        recorded.append(kwargs)

    monkeypatch.setattr("src.runtime.governance._record_run_event", fake_record)
    # record_context_compression_events 在 trace_recorder 内部用自己的 _record_run_event，这里也拦下来
    monkeypatch.setattr("src.server.routes.chat.trace_recorder._record_run_event", fake_record)

    state = AgentState()
    state.middle_context[COMPRESSION_KEY] = {
        "count": 1,
        "pending": [{"index": 1, "messagesBefore": 9, "messagesAfter": 3, "summaryCharsAfter": 200}],
    }
    agent = SimpleNamespace(_system_prompt="p" * 40, state=state, model=SimpleNamespace(context_size=128_000))

    ignored = asyncio.run(governance.observe_model_call_event(
        agent=agent, event_type="TEXT_BLOCK_DELTA", event_data={}, session_id="s", persist=True,
        scope="team_member", agent_id="m1", agent_name="Member",
    ))
    assert ignored == {}

    stats = asyncio.run(governance.observe_model_call_event(
        agent=agent, event_type="MODEL_CALL_END", event_data={"input_tokens": 120, "output_tokens": 5},
        session_id="s", persist=True, scope="team_member", agent_id="m1", agent_name="Member",
    ))
    assert stats["inputTokens"] == 120
    assert stats["compressionCount"] == 1
    types = [item["event_type"] for item in recorded]
    assert types == ["CONTEXT_COMPRESSED", "CONTEXT_USAGE"]
    assert recorded[1]["scope"] == "team_member"
    assert recorded[1]["payload"]["contextWindow"] == 128_000


def test_memory_prompt_and_recall_split(monkeypatch) -> None:
    from src.server.routes.chat.memory_prompt_cache import invalidate_memory_prompts

    invalidate_memory_prompts()
    monkeypatch.setattr("src.runtime.governance.build_habit_prompt", lambda *args, **kwargs: "# Habits\n摘要")
    monkeypatch.setattr("src.runtime.governance.build_professional_prompt", lambda *args, **kwargs: "")
    prompt, recall = governance.build_memory_prompt_and_recall(
        agent_id="a", agent_name="A", workspace_id="w", project_id="p",
        query_text="汇总销售表", freeze_session_id=None, exclude_session_id="s-1",
    )
    assert "# Habits" in prompt
    assert recall is None

    prompt, recall = governance.build_memory_prompt_and_recall(
        agent_id="a", agent_name="A", workspace_id="w", project_id="p",
        query_text="汇总销售表", freeze_session_id=None, include_markdown=False,
    )
    assert prompt == ""
    assert recall is None
    assert governance.append_user_note("正文", "") == "正文"
