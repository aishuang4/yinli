"""模型审核门：判定契约、Hermes 式上下文和技能触发。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from src.memory.extraction_agents import SkillDraftPayload, SkillEvolutionAgentPayload
from src.memory.markdown_store import MarkdownMemoryStore
from src.memory.model_review import (
    ModelReviewDecision,
    ModelReviewRequest,
    ReviewContextMessage,
    ReviewContextSnapshot,
    SKILL_REVIEW_CRITERIA,
    build_skill_review_prompt,
    deterministic_skill_gate,
    parse_model_review_text,
    review,
    skill_review_request,
    snapshot_review_context,
)
from src.memory.reflection import MemoryReflectionInput, reflect_completed_run_memory_with_agents
from src.server.routes.chat.models import ChatSkillRun, ChatToolRun
from src.storage import database


def test_review_request_does_not_import_admission_types():
    request = skill_review_request(ReviewContextSnapshot(messages=(ReviewContextMessage("user", "做完了"),)))
    assert isinstance(request, ModelReviewRequest)
    assert request.purpose == "skill_evolution_trigger"
    assert request.allow_criteria
    assert request.deny_criteria


def test_injected_reviewer_can_allow_or_deny():
    request = skill_review_request(ReviewContextSnapshot())

    async def allow(item: ModelReviewRequest) -> ModelReviewDecision:
        assert item.purpose == "skill_evolution_trigger"
        return ModelReviewDecision(allow=True, reason="有可重复做法", confidence=0.8, source="model")

    async def deny(item: ModelReviewRequest) -> ModelReviewDecision:
        del item
        return ModelReviewDecision(allow=False, reason="没有新手法", confidence=0.4, source="model")

    allowed = asyncio.run(review(request, reviewer=allow))
    denied = asyncio.run(review(request, reviewer=deny))
    assert allowed.allow is True
    assert denied.allow is False


def test_prompt_uses_compressed_context_and_hermes_criteria():
    snapshot = snapshot_review_context(
        SimpleNamespace(
            state=SimpleNamespace(
                summary="之前已经把发布流程整理过。",
                context=[
                    SimpleNamespace(role="user", content="请把发布步骤固定下来，以后都这样发。"),
                    SimpleNamespace(
                        role="assistant",
                        content=[
                            SimpleNamespace(type="thinking", text="不要出现"),
                            SimpleNamespace(type="text", text="<think>隐藏</think>已写好发布步骤。"),
                        ],
                    ),
                ],
            )
        ),
        [
            ChatSkillRun(
                id="skill-1",
                name="发布",
                content="这是不应该出现的 SKILL 正文",
                status="completed",
            )
        ],
    )
    prompt = build_skill_review_prompt(snapshot)
    assert "之前已经把发布流程整理过。" in prompt
    assert "请把发布步骤固定下来" in prompt
    assert "已写好发布步骤。" in prompt
    assert "很久以前的聊天原文" not in prompt
    assert "这是不应该出现的 SKILL 正文" not in prompt
    assert "隐藏" not in prompt
    assert "不要出现" not in prompt
    assert "skill-1: 发布" in prompt
    assert "skill_manage" not in SKILL_REVIEW_CRITERIA
    assert "skill_view" not in SKILL_REVIEW_CRITERIA
    assert "先读后写" not in SKILL_REVIEW_CRITERIA
    assert "這一類任務" in prompt
    assert "不要放行" in prompt


def test_sensitive_turn_does_not_need_a_model():
    decision = deterministic_skill_gate(
        user_message="api_key=sk-abcdefghijklmnopqrstuvwxyz",
        assistant_text="已记下。",
    )
    assert decision is not None
    assert decision.allow is False
    assert decision.source == "rule"


def test_unparseable_model_text_is_an_error():
    try:
        parse_model_review_text("不是 JSON")
    except Exception as exc:
        assert "allow" in str(exc)
    else:
        raise AssertionError("expected parse failure")


def test_model_allow_without_keywords_still_drafts(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "agent_workstation.db")
    monkeypatch.setattr(database, "_initialized", False)
    import src.server.routes.skills as skills_routes

    original = dict(skills_routes._skills)
    monkeypatch.setattr(skills_routes, "SKILL_PACKAGES_DIR", str(tmp_path / "skill_packages"))

    async def allow_review(**kwargs):
        assert "压缩摘要" in kwargs["user_prompt"] or "user:" in kwargs["user_prompt"]
        assert "skill_manage" not in kwargs["user_prompt"]
        return '{"allow": true, "confidence": 0.9, "reason": "有可重复的发布步骤"}'

    async def fake_skill_agent(**kwargs):
        assert kwargs["router_gate"].should_evaluate is True
        return SkillEvolutionAgentPayload(
            reusable=True,
            confidence=0.9,
            granularity="atomic",
            reason="可以沉淀。",
            draft=SkillDraftPayload(
                name="发布步骤",
                description="按固定顺序发布。",
                trigger="当需要发布时使用。",
                content="1. 构建。\n2. 发布。",
                tags=["release"],
            ),
        )

    monkeypatch.setattr("src.memory.extraction_agents.run_model_review_agent", allow_review)
    monkeypatch.setattr("src.memory.reflection.run_skill_evolution_agent", fake_skill_agent)
    monkeypatch.setattr("src.memory.reflection.run_memory_reflection_agent", lambda **kwargs: _none())

    try:
        result = asyncio.run(
            reflect_completed_run_memory_with_agents(
                MemoryReflectionInput(
                    session_id="session-1",
                    agent_id="agent-1",
                    agent_name="Agent",
                    user_message="把发布顺序定下来。",
                    assistant_text="好，就按构建再发布。",
                    review_context=ReviewContextSnapshot(
                        summary="之前讨论过同一套发布。",
                        messages=(ReviewContextMessage("user", "把发布顺序定下来。"),),
                    ),
                ),
                model_name="model",
                include_markdown=False,
                store=MarkdownMemoryStore(root=tmp_path / "memory"),
            )
        )
    finally:
        skills_routes._skills.clear()
        skills_routes._skills.update(original)

    assert result.skill_evolution_record is not None
    assert result.skill_evolution_record["status"] == "registered"
    assert "source:model" in result.skill_evolution_record["routerGate"]["signals"]


def test_model_deny_skips_draft_and_keeps_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "agent_workstation.db")
    monkeypatch.setattr(database, "_initialized", False)

    async def deny_review(**kwargs):
        del kwargs
        return '{"allow": false, "confidence": 0.2, "reason": "这次没有新手法"}'

    async def fake_skill_agent(**kwargs):
        raise AssertionError("denied review must not draft")

    monkeypatch.setattr("src.memory.extraction_agents.run_model_review_agent", deny_review)
    monkeypatch.setattr("src.memory.reflection.run_skill_evolution_agent", fake_skill_agent)

    result = asyncio.run(
        reflect_completed_run_memory_with_agents(
            MemoryReflectionInput(
                session_id="session-1",
                agent_id="agent-1",
                agent_name="Agent",
                user_message="今天市场怎么样？",
                assistant_text="涨了一点，这是一次性问答。",
            ),
            model_name="model",
            include_markdown=False,
            store=MarkdownMemoryStore(root=tmp_path / "memory"),
        )
    )
    assert result.skill_candidate is None
    assert result.skill_evolution_record["status"] == "skipped"
    assert result.skill_evolution_record["routerGate"]["shouldEvaluate"] is False
    assert "没有新手法" in result.skill_evolution_record["routerGate"]["reason"]


def test_model_failure_falls_back_to_keyword_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "agent_workstation.db")
    monkeypatch.setattr(database, "_initialized", False)

    async def broken_review(**kwargs):
        del kwargs
        return "不是 JSON"

    async def fake_skill_agent(**kwargs):
        raise AssertionError("keyword fallback should reject this turn")

    monkeypatch.setattr("src.memory.extraction_agents.run_model_review_agent", broken_review)
    monkeypatch.setattr("src.memory.reflection.run_skill_evolution_agent", fake_skill_agent)

    result = asyncio.run(
        reflect_completed_run_memory_with_agents(
            MemoryReflectionInput(
                session_id="session-1",
                agent_id="agent-1",
                agent_name="Agent",
                user_message="帮我看一下这个函数。",
                assistant_text="可以。",
            ),
            model_name="model",
            include_markdown=False,
            store=MarkdownMemoryStore(root=tmp_path / "memory"),
        )
    )
    assert result.skill_evolution_record["routerGate"]["shouldEvaluate"] is False
    assert "source:fallback" in result.skill_evolution_record["routerGate"]["signals"]
    assert result.skill_evolution_record["routerGate"]["reason"].startswith("[fallback]")


async def _none():
    return None
