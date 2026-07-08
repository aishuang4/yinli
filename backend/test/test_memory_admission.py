"""记忆准入门禁测试。"""

from __future__ import annotations

from src.memory.admission import (
    MemoryAdmissionInput,
    MemoryAdmissionScores,
    apply_model_admission_scores,
    evaluate_memory_admission,
)


def test_memory_admission_accepts_substantive_history_only_turn():
    decision = evaluate_memory_admission(
        MemoryAdmissionInput(
            user_message="帮我检查这个记忆模块的实现方案。",
            assistant_text="已经检查完成，并给出下一步修改建议。",
        )
    )

    assert decision.should_persist_history is True
    assert decision.should_write_markdown is False
    assert decision.should_evaluate_skill is False
    assert decision.reason == "no_long_term_intent"
    assert decision.scores.history >= decision.policy.history_threshold
    assert decision.scores.skill < decision.policy.skill_threshold


def test_memory_admission_detects_long_term_markdown_memory():
    decision = evaluate_memory_admission(
        MemoryAdmissionInput(
            user_message="以后默认用中文回答，解释保持简洁。",
            assistant_text="好的，我会记住这个偏好。",
        )
    )

    assert decision.should_persist_history is True
    assert decision.should_write_markdown is True
    assert "long_term_intent" in decision.signals


def test_memory_admission_skips_sensitive_content():
    decision = evaluate_memory_admission(
        MemoryAdmissionInput(
            user_message="记住 token=secret-value-123456",
            assistant_text="不应该保存。",
        )
    )

    assert decision.should_persist_history is False
    assert decision.should_write_markdown is False
    assert decision.should_evaluate_skill is False
    assert decision.sensitive is True
    assert decision.reason == "sensitive_content"
    assert "<redacted>" in decision.redacted_user_message
    assert decision.redaction_types["credential_assignment"] == 1


def test_memory_admission_skips_trivial_turn():
    decision = evaluate_memory_admission(
        MemoryAdmissionInput(
            user_message="谢谢",
            assistant_text="好的",
        )
    )

    assert decision.should_persist_history is False
    assert decision.should_write_markdown is False
    assert decision.reason == "trivial_turn"


def test_memory_admission_keeps_manual_skill_signal_for_short_command():
    decision = evaluate_memory_admission(
        MemoryAdmissionInput(
            user_message="把这个生成 Skill",
            assistant_text="已开始生成。",
            manual_skill_request=True,
        )
    )

    assert decision.should_evaluate_skill is True
    assert "manual_skill_request" in decision.signals


def test_memory_admission_audit_payload_contains_scores_and_thresholds():
    decision = evaluate_memory_admission(
        MemoryAdmissionInput(
            user_message="以后默认用中文回答，解释保持简洁。",
            assistant_text="好的，我会记住这个偏好。",
        )
    )

    payload = decision.audit_payload()

    assert payload["reason"] == "accepted"
    assert payload["scores"]["markdown"] >= payload["thresholds"]["markdown"]
    assert payload["redactedUserMessage"]


def test_memory_admission_audit_payload_contains_redaction_types():
    decision = evaluate_memory_admission(
        MemoryAdmissionInput(
            user_message="记住我的邮箱 test@example.com 和手机号 13800138000。",
            assistant_text="不应该保存。",
        )
    )

    payload = decision.audit_payload()

    assert payload["redactionCount"] == 2
    assert payload["redactionTypes"]["email"] == 1
    assert payload["redactionTypes"]["phone_cn"] == 1


def test_memory_admission_merges_model_scores():
    decision = evaluate_memory_admission(
        MemoryAdmissionInput(
            user_message="帮我检查这个记忆模块的实现方案。",
            assistant_text="已经检查完成，并给出下一步修改建议。",
        )
    )

    merged = apply_model_admission_scores(
        decision,
        scores=MemoryAdmissionScores(history=0.9, markdown=0.9, skill=0.9),
        reason="模型认为该方案具有长期复用价值。",
    )
    payload = merged.audit_payload()

    assert merged.scores.markdown > decision.scores.markdown
    assert merged.scores.skill > decision.scores.skill
    assert "model_scoring" in merged.signals
    assert payload["modelScoring"]["scores"]["markdown"] == 0.9
