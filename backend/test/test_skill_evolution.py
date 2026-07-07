"""Skill 自进化 router gate 与 workflow evidence 测试。"""

from __future__ import annotations

import asyncio

from src.memory.skill_evolution import (
    SkillEvolutionRecordInput,
    build_workflow_evidence,
    evaluate_skill_evolution_router_gate,
    persist_skill_evolution_record,
    register_skill_evolution_record,
)
from src.server.routes.memory import skill_evolution_records
from src.server.routes.chat.models import ChatToolRun
from src.storage import database


def test_skill_evolution_record_persists_draft_candidate(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "agent_workstation.db")
    monkeypatch.setattr(database, "_initialized", False)
    gate = evaluate_skill_evolution_router_gate(
        user_message="帮我实现一个可复用的记忆提炼流程。",
        assistant_text="已完成实现，并通过测试验证。",
        tool_calls=[
            ChatToolRun(
                id="call-1",
                name="search_user",
                args='{"email":"alice@example.com"}',
                result='{"user_id":"12345"}',
                status="completed",
            ),
            ChatToolRun(
                id="call-2",
                name="send_email",
                args='{"user_id":"12345"}',
                result='{"message_id":"msg-1"}',
                status="completed",
            ),
        ],
    )
    evidence = build_workflow_evidence(
        tool_calls=[
            ChatToolRun(
                id="call-1",
                name="search_user",
                args='{"email":"alice@example.com"}',
                result='{"user_id":"12345"}',
                status="completed",
            ),
            ChatToolRun(
                id="call-2",
                name="send_email",
                args='{"user_id":"12345"}',
                result='{"message_id":"msg-1"}',
                status="completed",
            ),
        ],
    )
    candidate = _Candidate()

    record = persist_skill_evolution_record(
        SkillEvolutionRecordInput(
            session_id="session-1",
            agent_id="agent-1",
            agent_name="Agent",
            user_message="帮我实现一个可复用的记忆提炼流程。",
            assistant_text="已完成实现，并通过测试验证。",
            source_run_id="run-1",
            router_gate=gate,
            workflow_evidence=evidence,
            candidate=candidate,
            status="draft",
        ),
    )

    assert record["status"] == "draft"
    assert record["draftSkillName"] == "用户发送邮件"
    assert record["inputsSchema"]["properties"]["user_id"]["type"] == "string"
    assert record["variableFlow"][0]["target"] == "send_email.user_id"


def test_memory_route_lists_skill_evolution_records(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "agent_workstation.db")
    monkeypatch.setattr(database, "_initialized", False)
    _draft_record(tmp_path, monkeypatch, source_run_id="run-list-1")

    response = asyncio.run(skill_evolution_records(status="draft", agent_id="agent-1", limit=10))

    assert len(response.records) == 1
    assert response.records[0]["status"] == "draft"
    assert response.records[0]["draftSkillName"] == "用户发送邮件"


def test_skill_evolution_record_registers_standard_skill(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "agent_workstation.db")
    monkeypatch.setattr(database, "_initialized", False)
    import src.server.routes.skills as skills_routes

    original_skills = dict(skills_routes._skills)
    monkeypatch.setattr(skills_routes, "SKILL_PACKAGES_DIR", str(tmp_path / "skill_packages"))
    try:
        record = _draft_record(tmp_path, monkeypatch, source_run_id="run-register-1")

        updated, reason = register_skill_evolution_record(record)

        assert reason == ""
        assert updated is not None
        assert updated["status"] == "registered"
        assert updated["registeredSkillId"]
        skill = skills_routes._skills[updated["registeredSkillId"]]
        assert skill.category == "自进化"
        assert skill.enabled is True
        assert skill.resource_path
        assert skill.resources == ["SKILL.md"]
        assert (tmp_path / "skill_packages" / skill.name / "SKILL.md").exists()
    finally:
        skills_routes._skills.clear()
        skills_routes._skills.update(original_skills)


def test_skill_evolution_record_does_not_register_duplicate_skill(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "agent_workstation.db")
    monkeypatch.setattr(database, "_initialized", False)
    import src.server.routes.skills as skills_routes

    original_skills = dict(skills_routes._skills)
    monkeypatch.setattr(skills_routes, "SKILL_PACKAGES_DIR", str(tmp_path / "skill_packages"))
    try:
        first = _draft_record(tmp_path, monkeypatch, source_run_id="run-register-2")
        updated, reason = register_skill_evolution_record(first)
        assert updated is not None
        assert reason == ""
        before_count = len(skills_routes._skills)

        duplicate = _draft_record(tmp_path, monkeypatch, source_run_id="run-register-3")
        second, reason = register_skill_evolution_record(duplicate)

        assert second is not None
        assert reason.startswith("merged_similar_skill:")
        assert second["status"] == "registered"
        assert second["registeredSkillId"] == updated["registeredSkillId"]
        assert len(skills_routes._skills) == before_count
    finally:
        skills_routes._skills.clear()
        skills_routes._skills.update(original_skills)


def test_skill_evolution_record_merges_semantically_similar_skill(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "agent_workstation.db")
    monkeypatch.setattr(database, "_initialized", False)
    import src.server.routes.skills as skills_routes

    original_skills = dict(skills_routes._skills)
    monkeypatch.setattr(skills_routes, "SKILL_PACKAGES_DIR", str(tmp_path / "skill_packages"))
    try:
        first = _draft_record(tmp_path, monkeypatch, source_run_id="run-register-4")
        updated, reason = register_skill_evolution_record(first)
        assert updated is not None
        assert reason == ""
        before_count = len(skills_routes._skills)

        similar = _draft_record(
            tmp_path,
            monkeypatch,
            source_run_id="run-register-5",
            candidate=_SimilarCandidate(),
        )
        second, reason = register_skill_evolution_record(similar)

        assert second is not None
        assert reason.startswith("merged_similar_skill:")
        assert second["status"] == "registered"
        assert second["registeredSkillId"] == updated["registeredSkillId"]
        assert len(skills_routes._skills) == before_count
    finally:
        skills_routes._skills.clear()
        skills_routes._skills.update(original_skills)


class _Draft:
    name = "用户发送邮件"
    description = "根据用户查询结果发送邮件。"
    trigger = "需要先查询用户再发送邮件时使用。"
    content = "1. 查询用户。\n2. 使用 user_id 发送邮件。"
    tags = ["user", "email"]


class _Candidate:
    reusable = True
    confidence = 0.86
    granularity = "atomic"
    requires_workflow = False
    reason = "形成稳定工具链。"
    suggested_split = []
    variable_flow = []
    draft = _Draft()


class _SimilarDraft:
    name = "客户邮件通知"
    description = "先查询客户信息，再基于返回的 user_id 发送邮件通知。"
    trigger = "当任务需要查找用户并发送邮件通知时使用。"
    content = "1. 使用 search_user 查询目标用户。\n2. 从结果中提取 user_id。\n3. 调用 send_email 发送邮件，禁止硬编码 user_id。"
    tags = ["email", "notification", "user"]


class _SimilarCandidate:
    reusable = True
    confidence = 0.86
    granularity = "atomic"
    requires_workflow = False
    reason = "形成稳定工具链。"
    suggested_split = []
    variable_flow = []
    draft = _SimilarDraft()


def _draft_record(tmp_path, monkeypatch, *, source_run_id: str, candidate=None) -> dict:
    gate = evaluate_skill_evolution_router_gate(
        user_message="帮我实现一个可复用的用户发送邮件 Skill。",
        assistant_text="已完成实现，并通过测试验证。",
        tool_calls=[
            ChatToolRun(
                id="call-1",
                name="send_email",
                args='{"user_id":"12345"}',
                result="sent",
                status="completed",
            ),
        ],
    )
    evidence = build_workflow_evidence(
        tool_calls=[
            ChatToolRun(
                id="call-1",
                name="send_email",
                args='{"user_id":"12345"}',
                result="sent",
                status="completed",
            ),
        ],
    )
    return persist_skill_evolution_record(
        SkillEvolutionRecordInput(
            session_id="session-1",
            agent_id="agent-1",
            agent_name="Agent",
            user_message="帮我实现一个可复用的用户发送邮件 Skill。",
            assistant_text="已完成实现，并通过测试验证。",
            source_run_id=source_run_id,
            router_gate=gate,
            workflow_evidence=evidence,
            candidate=candidate or _Candidate(),
            status="draft",
        ),
    )


def test_skill_evolution_gate_accepts_completed_reusable_work_with_tools():
    decision = evaluate_skill_evolution_router_gate(
        user_message="帮我实现一个可复用的记忆提炼流程。",
        assistant_text="已完成实现，并通过测试验证。",
        tool_calls=[
            ChatToolRun(
                id="call-1",
                name="Edit",
                args='{"file_path":"backend/src/memory/reflection.py"}',
                result="updated",
                status="completed",
            )
        ],
    )

    assert decision.should_evaluate is True
    assert decision.risk == "low"
    assert "successful_tool_chain" in decision.signals


def test_skill_evolution_gate_rejects_short_non_reusable_turn():
    decision = evaluate_skill_evolution_router_gate(
        user_message="今天几点？",
        assistant_text="下午三点。",
        tool_calls=[],
    )

    assert decision.should_evaluate is False
    assert decision.risk == "skip"


def test_skill_evolution_gate_rejects_sensitive_content():
    decision = evaluate_skill_evolution_router_gate(
        user_message="帮我记住 token=secret-value-123456",
        assistant_text="好的。",
        tool_calls=[],
    )

    assert decision.should_evaluate is False
    assert decision.risk == "high"
    assert "sensitive_content" in decision.signals


def test_workflow_evidence_splits_successful_tools_and_failures():
    evidence = build_workflow_evidence(
        tool_calls=[
            ChatToolRun(
                id="call-read",
                name="Read",
                args='{"file_path":"backend/src/memory/extraction_agents.py"}',
                result="读取成功",
                status="completed",
            ),
            ChatToolRun(
                id="call-pytest",
                name="Bash",
                args='{"cmd":"uv run pytest test/test_memory_reflection.py"}',
                result="21 passed",
                status="completed",
            ),
            ChatToolRun(
                id="call-bad",
                name="Bash",
                args='{"cmd":"bad command"}',
                result="unsupported option",
                status="failed",
            ),
            ChatToolRun(
                id="call-noise",
                name="Read",
                args='{"file_path":"/tmp/missing"}',
                result="",
                status="failed",
            ),
        ],
    )

    assert evidence.tool_call_count == 4
    assert [item.tool for item in evidence.successful_tool_chain] == ["Read", "Bash"]
    assert "backend/src/memory/extraction_agents.py" in evidence.changed_files
    assert "uv run pytest" in evidence.tests
    assert len(evidence.useful_failures) == 1
    assert evidence.discarded_failures.count == 1


def test_workflow_evidence_extracts_variable_flow_without_hardcoding_values():
    evidence = build_workflow_evidence(
        tool_calls=[
            ChatToolRun(
                id="call-search",
                name="search_user",
                args='{"email":"alice@example.com"}',
                result='{"user_id":"12345","name":"Alice"}',
                status="completed",
            ),
            ChatToolRun(
                id="call-email",
                name="send_email",
                args='{"user_id":"12345","template":"welcome"}',
                result='{"message_id":"msg-1","sent":true}',
                status="completed",
            ),
        ],
    )

    assert evidence.successful_tool_chain[0].input_summary["email"] == "<email>"
    assert "user_id" in evidence.successful_tool_chain[0].output_variables
    assert evidence.successful_tool_chain[1].input_summary["user_id"] == "<user_id>"
    assert evidence.successful_tool_chain[1].depends_on == ["search_user.user_id"]
    assert evidence.variable_flow[0].source == "search_user.user_id"
    assert evidence.variable_flow[0].target == "send_email.user_id"
