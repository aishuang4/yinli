"""记忆准入门禁。

集中判断一轮完成对话是否应该进入历史记忆、Markdown 长期记忆和 Skill 自进化。
首版使用保守规则，后续可替换为模型 gate 或配置化策略。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


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
_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(api[_-]?key|token|password|secret|cookie)\b\s*[:=]\s*\S+"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\bsk-[a-z0-9_-]{12,}"),
)


@dataclass(frozen=True)
class MemoryAdmissionDecision:
    """单轮对话的记忆准入决策。"""

    should_persist_history: bool
    should_write_markdown: bool
    should_evaluate_skill: bool
    reason: str
    signals: list[str] = field(default_factory=list)
    sensitive: bool = False


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

    user_message = _normalize_text(item.user_message)
    assistant_text = _normalize_text(item.assistant_text)
    if not user_message or not assistant_text:
        return MemoryAdmissionDecision(
            should_persist_history=False,
            should_write_markdown=False,
            should_evaluate_skill=False,
            reason="empty_turn",
        )

    if _contains_sensitive_content(user_message) or _contains_sensitive_content(assistant_text):
        return MemoryAdmissionDecision(
            should_persist_history=False,
            should_write_markdown=False,
            should_evaluate_skill=False,
            reason="sensitive_content",
            signals=["sensitive"],
            sensitive=True,
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

    if _is_trivial_turn(user_message, assistant_text):
        return MemoryAdmissionDecision(
            should_persist_history=False,
            should_write_markdown=False,
            should_evaluate_skill=bool(item.manual_skill_request),
            reason="trivial_turn",
            signals=signals,
        )

    return MemoryAdmissionDecision(
        should_persist_history=True,
        should_write_markdown=long_term,
        should_evaluate_skill=True,
        reason="accepted" if long_term else "no_long_term_intent",
        signals=signals,
    )


def _has_long_term_intent(text: str) -> bool:
    lowered = text.lower()
    return any(hint in lowered for hint in _LONG_TERM_HINTS)


def _contains_sensitive_content(text: str) -> bool:
    return any(pattern.search(text) for pattern in _SECRET_PATTERNS)


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
