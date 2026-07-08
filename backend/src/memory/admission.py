"""记忆准入门禁。

集中判断一轮完成对话是否应该进入历史记忆、Markdown 长期记忆和 Skill 自进化。
首版使用保守规则，后续可替换为模型 gate 或配置化策略。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from src.memory.sanitizer import contains_sensitive_content, merge_redaction_types, redact_sensitive_text
from src.memory.settings import MemoryAdmissionPolicy, get_memory_admission_policy


_LONG_TERM_HINTS = (
    "以后",
    "下次",
    "默认",
    "记住",
    "长期",
    "一直",
    "每次",
    "不要再",
    "别再",
    "禁止",
)
_TRIVIAL_MESSAGES = {
    "ok",
    "okay",
    "thanks",
    "thank you",
    "谢谢",
    "好的",
    "收到",
    "明白",
    "嗯",
}
_SUBSTANTIVE_MARKERS = (
    "实现",
    "设计",
    "修复",
    "推进",
    "测试",
    "提交",
    "架构",
    "方案",
    "记忆",
    "skill",
    "agent",
)


@dataclass(frozen=True)
class MemoryAdmissionScores:
    """MemoryAdmissionGate 三类准入评分。"""

    history: float = 0.0
    markdown: float = 0.0
    skill: float = 0.0


@dataclass(frozen=True)
class MemoryAdmissionDecision:
    """单轮对话的记忆准入决策。"""

    should_persist_history: bool
    should_write_markdown: bool
    should_evaluate_skill: bool
    reason: str
    signals: list[str] = field(default_factory=list)
    sensitive: bool = False
    scores: MemoryAdmissionScores = field(default_factory=MemoryAdmissionScores)
    policy: MemoryAdmissionPolicy = field(default_factory=MemoryAdmissionPolicy)
    redacted_user_message: str = ""
    redacted_assistant_text: str = ""
    model_scores: MemoryAdmissionScores | None = None
    model_reason: str = ""
    model_error: str = ""
    redaction_count: int = 0
    redaction_types: dict[str, int] = field(default_factory=dict)

    def audit_payload(self) -> dict[str, Any]:
        """转换为可写入审计事件的稳定 payload。"""

        return {
            "reason": self.reason,
            "signals": self.signals,
            "sensitive": self.sensitive,
            "scores": {
                "history": self.scores.history,
                "markdown": self.scores.markdown,
                "skill": self.scores.skill,
            },
            "thresholds": {
                "history": self.policy.history_threshold,
                "markdown": self.policy.markdown_threshold,
                "skill": self.policy.skill_threshold,
            },
            "modelScoring": {
                "enabled": self.policy.enable_model_scoring,
                "scores": (
                    {
                        "history": self.model_scores.history,
                        "markdown": self.model_scores.markdown,
                        "skill": self.model_scores.skill,
                    }
                    if self.model_scores
                    else {}
                ),
                "reason": self.model_reason,
                "error": self.model_error,
                "weight": self.policy.model_score_weight,
            },
            "redactedUserMessage": self.redacted_user_message[:500],
            "redactedAssistantText": self.redacted_assistant_text[:500],
            "redactionCount": self.redaction_count,
            "redactionTypes": self.redaction_types,
        }


@dataclass(frozen=True)
class MemoryAdmissionInput:
    """记忆准入输入。"""

    user_message: str
    assistant_text: str
    tool_calls: list[object] = field(default_factory=list)
    runtime_events: list[dict] = field(default_factory=list)
    manual_skill_request: bool = False


def evaluate_memory_admission(item: MemoryAdmissionInput) -> MemoryAdmissionDecision:
    """用轻量规则判断当前对话是否进入各类记忆链路。"""

    policy = get_memory_admission_policy()
    user_message = _normalize_text(item.user_message)
    assistant_text = _normalize_text(item.assistant_text)
    user_redaction = redact_sensitive_text(user_message) if policy.enable_redaction else None
    assistant_redaction = redact_sensitive_text(assistant_text) if policy.enable_redaction else None
    redacted_user = user_redaction.text if user_redaction else user_message
    redacted_assistant = assistant_redaction.text if assistant_redaction else assistant_text
    redaction_count = (user_redaction.count if user_redaction else 0) + (
        assistant_redaction.count if assistant_redaction else 0
    )
    redaction_types = (
        merge_redaction_types(user_redaction, assistant_redaction)
        if user_redaction and assistant_redaction
        else {}
    )
    if not user_message or not assistant_text:
        return MemoryAdmissionDecision(
            should_persist_history=False,
            should_write_markdown=False,
            should_evaluate_skill=False,
            reason="empty_turn",
            policy=policy,
            redacted_user_message=redacted_user,
            redacted_assistant_text=redacted_assistant,
            redaction_count=redaction_count,
            redaction_types=redaction_types,
        )

    sensitive = contains_sensitive_content(user_message) or contains_sensitive_content(assistant_text)
    if policy.reject_sensitive and sensitive:
        return MemoryAdmissionDecision(
            should_persist_history=False,
            should_write_markdown=False,
            should_evaluate_skill=False,
            reason="sensitive_content",
            signals=["sensitive"],
            sensitive=True,
            policy=policy,
            redacted_user_message=redacted_user,
            redacted_assistant_text=redacted_assistant,
            redaction_count=redaction_count,
            redaction_types=redaction_types,
        )

    signals: list[str] = []
    long_term = _has_long_term_intent(user_message)
    if long_term:
        signals.append("long_term_intent")
    if item.tool_calls:
        signals.append("tool_calls")
    if item.runtime_events:
        signals.append("runtime_events")
    if item.manual_skill_request:
        signals.append("manual_skill_request")
    if _has_substantive_marker(user_message + "\n" + assistant_text):
        signals.append("substantive_marker")

    if _is_trivial_turn(user_message, assistant_text):
        return MemoryAdmissionDecision(
            should_persist_history=False,
            should_write_markdown=False,
            should_evaluate_skill=bool(item.manual_skill_request),
            reason="trivial_turn",
            signals=signals,
            policy=policy,
            redacted_user_message=redacted_user,
            redacted_assistant_text=redacted_assistant,
            redaction_count=redaction_count,
            redaction_types=redaction_types,
        )

    scores = _score_admission(
        user_message=user_message,
        assistant_text=assistant_text,
        signals=signals,
        policy=policy,
    )
    should_persist_history = scores.history >= policy.history_threshold
    should_write_markdown = scores.markdown >= policy.markdown_threshold
    should_evaluate_skill = scores.skill >= policy.skill_threshold
    reason = "accepted" if should_write_markdown else "no_long_term_intent"
    if not should_persist_history:
        reason = "below_history_threshold"

    return MemoryAdmissionDecision(
        should_persist_history=should_persist_history,
        should_write_markdown=should_write_markdown,
        should_evaluate_skill=should_evaluate_skill,
        reason=reason,
        signals=signals,
        sensitive=sensitive,
        scores=scores,
        policy=policy,
        redacted_user_message=redacted_user,
        redacted_assistant_text=redacted_assistant,
        redaction_count=redaction_count,
        redaction_types=redaction_types,
    )


def _has_long_term_intent(text: str) -> bool:
    lowered = text.lower()
    return any(hint in lowered for hint in _LONG_TERM_HINTS)


def apply_model_admission_scores(
    decision: MemoryAdmissionDecision,
    *,
    scores: MemoryAdmissionScores | None,
    reason: str = "",
    error: str = "",
) -> MemoryAdmissionDecision:
    """融合模型评分和规则评分，返回新的准入决策。"""

    if not scores:
        return _replace_decision(decision, model_reason=reason, model_error=error)
    weight = min(max(decision.policy.model_score_weight, 0), 1)
    merged = MemoryAdmissionScores(
        history=_blend(decision.scores.history, scores.history, weight),
        markdown=_blend(decision.scores.markdown, scores.markdown, weight),
        skill=_blend(decision.scores.skill, scores.skill, weight),
    )
    should_persist_history = merged.history >= decision.policy.history_threshold
    should_write_markdown = merged.markdown >= decision.policy.markdown_threshold
    should_evaluate_skill = merged.skill >= decision.policy.skill_threshold
    merged_reason = "accepted" if should_write_markdown else "no_long_term_intent"
    if not should_persist_history:
        merged_reason = "below_history_threshold"
    signals = list(decision.signals)
    if "model_scoring" not in signals:
        signals.append("model_scoring")
    return _replace_decision(
        decision,
        should_persist_history=should_persist_history,
        should_write_markdown=should_write_markdown,
        should_evaluate_skill=should_evaluate_skill,
        reason=merged_reason,
        signals=signals,
        scores=merged,
        model_scores=scores,
        model_reason=reason,
        model_error=error,
    )


def _blend(rule_score: float, model_score: float, weight: float) -> float:
    return min(max((rule_score * (1 - weight)) + (model_score * weight), 0), 1)


def _replace_decision(decision: MemoryAdmissionDecision, **updates: Any) -> MemoryAdmissionDecision:
    data = {
        "should_persist_history": decision.should_persist_history,
        "should_write_markdown": decision.should_write_markdown,
        "should_evaluate_skill": decision.should_evaluate_skill,
        "reason": decision.reason,
        "signals": decision.signals,
        "sensitive": decision.sensitive,
        "scores": decision.scores,
        "policy": decision.policy,
        "redacted_user_message": decision.redacted_user_message,
        "redacted_assistant_text": decision.redacted_assistant_text,
        "model_scores": decision.model_scores,
        "model_reason": decision.model_reason,
        "model_error": decision.model_error,
        "redaction_count": decision.redaction_count,
        "redaction_types": decision.redaction_types,
    }
    data.update(updates)
    return MemoryAdmissionDecision(**data)

def _has_substantive_marker(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _SUBSTANTIVE_MARKERS)


def _score_admission(
    *,
    user_message: str,
    assistant_text: str,
    signals: list[str],
    policy: MemoryAdmissionPolicy,
) -> MemoryAdmissionScores:
    user_len = len(user_message)
    assistant_len = len(assistant_text)
    history = 0.25
    if user_len >= policy.min_user_chars and assistant_len >= policy.min_assistant_chars:
        history += 0.18
    if "substantive_marker" in signals:
        history += 0.18
    if "tool_calls" in signals or "runtime_events" in signals:
        history += 0.14
    if "long_term_intent" in signals:
        history += 0.2

    markdown = 0.12
    if "long_term_intent" in signals:
        markdown += 0.58
    if "substantive_marker" in signals:
        markdown += 0.08
    if any(marker in user_message for marker in ("不要", "禁止", "默认", "以后", "下次")):
        markdown += 0.1

    skill = 0.2
    if "manual_skill_request" in signals:
        skill += 0.55
    if "tool_calls" in signals or "runtime_events" in signals:
        skill += 0.2
    if "substantive_marker" in signals:
        skill += 0.12

    return MemoryAdmissionScores(
        history=min(history, 1.0),
        markdown=min(markdown, 1.0),
        skill=min(skill, 1.0),
    )


def _is_trivial_turn(user_message: str, assistant_text: str) -> bool:
    user = user_message.strip().lower()
    assistant = assistant_text.strip().lower()
    if user in _TRIVIAL_MESSAGES and len(assistant) <= 20:
        return True
    if assistant in _TRIVIAL_MESSAGES and len(user) <= 8:
        return True
    return False


def _normalize_text(value: str) -> str:
    return " ".join(value.split())
