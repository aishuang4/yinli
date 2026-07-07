"""记忆准入门禁测试。"""

from __future__ import annotations

from src.memory.admission import MemoryAdmissionInput, evaluate_memory_admission


def test_memory_admission_accepts_substantive_history_only_turn():
    decision = evaluate_memory_admission(
        MemoryAdmissionInput(
            user_message="帮我检查这个记忆模块的实现方案。",
            assistant_text="已经检查完成，并给出下一步修改建议。",
        )
    )

    assert decision.should_persist_history is True
    assert decision.should_write_markdown is False
    assert decision.should_evaluate_skill is True
    assert decision.reason == "no_long_term_intent"


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
