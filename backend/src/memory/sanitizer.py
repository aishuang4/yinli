"""记忆系统敏感信息过滤。

所有准入、Markdown 写入和内部记忆工具都应复用这里的规则，避免多套脱敏逻辑漂移。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class RedactionResult:
    """单次脱敏结果。"""

    text: str
    count: int = 0
    types: dict[str, int] = field(default_factory=dict)

    @property
    def sensitive(self) -> bool:
        return self.count > 0


_SENSITIVE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "private_key",
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
    ),
    (
        # JSON 形式的凭据（工具入参 / HTTP 头），例如 "Authorization": "Bearer xxx"、"apiKey": "..."
        "json_credential",
        re.compile(
            r"(?i)\"(api[_-]?key|access[_-]?token|refresh[_-]?token|token|password|secret|cookie|authorization|x-api-key)\""
            r"\s*:\s*\"[^\"]*\"",
        ),
    ),
    (
        "credential_assignment",
        re.compile(r"(?i)\b(api[_-]?key|token|password|secret|cookie)\b\s*[:=]\s*\S+"),
    ),
    (
        "bearer_token",
        re.compile(r"(?i)\b(authorization)\b\s*[:=]\s*bearer\s+\S+"),
    ),
    (
        "openai_key",
        re.compile(r"(?i)\bsk-[a-z0-9_-]{12,}"),
    ),
    (
        "email",
        re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"),
    ),
    (
        "phone_cn",
        re.compile(r"\b1[3-9]\d{9}\b"),
    ),
    (
        "payment_card_like",
        re.compile(r"\b(?:\d[ -]*?){13,19}\b"),
    ),
)


def redact_sensitive_text(text: str) -> RedactionResult:
    """返回脱敏文本和命中类型统计。"""

    result = str(text or "")
    types: dict[str, int] = {}
    total = 0
    for kind, pattern in _SENSITIVE_PATTERNS:
        result, changed = pattern.subn("<redacted>", result)
        if changed:
            types[kind] = types.get(kind, 0) + changed
            total += changed
    return RedactionResult(text=result, count=total, types=types)


def contains_sensitive_content(text: str) -> bool:
    """判断文本是否包含敏感信息。"""

    return any(pattern.search(str(text or "")) for _kind, pattern in _SENSITIVE_PATTERNS)


def merge_redaction_types(*items: RedactionResult) -> dict[str, int]:
    """合并多段文本的脱敏类型统计。"""

    result: dict[str, int] = {}
    for item in items:
        for kind, count in item.types.items():
            result[kind] = result.get(kind, 0) + count
    return result


def sanitize_memory_note_text(text: str) -> str:
    """清洗准备写入 Markdown 的单条记忆。"""

    redacted = redact_sensitive_text(text).text
    redacted = re.sub(r"\s+", " ", redacted).strip()
    if redacted == "<redacted>" or not redacted.replace("-", "").strip():
        return ""
    return redacted
