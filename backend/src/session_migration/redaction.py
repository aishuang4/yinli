"""遷移導出的密文脫敏。明文 token 不得進入備份、報告或 Session 檔。"""

from __future__ import annotations

import json
import re
from typing import Any

_ASSIGNED_SECRET = re.compile(
    r"(?i)((?:api[_-]?key|token|secret|password|authorization|credential|auth_config)\s*[:=]\s*)(\S+)"
)
_SK = re.compile(r"\b(?:sk|k2a)[-_][A-Za-z0-9]{6,}\b")
_BEARER = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{8,}")
_SENSITIVE_KEY = re.compile(r"(?i)api[_-]?key|token|secret|password|authorization|credential")
REDACTED = "[redacted]"


def redact_text(value: str) -> str:
    """替換常見明文 token，保留 secret: 引用。"""

    def assigned(match: re.Match[str]) -> str:
        raw = match.group(2)
        if raw.startswith("secret:"):
            return match.group(0)
        return match.group(1) + REDACTED

    text = _ASSIGNED_SECRET.sub(assigned, value)
    text = _BEARER.sub(lambda match: match.group(1) + REDACTED, text)
    return _SK.sub(REDACTED, text)


def redact_json(value: Any) -> Any:
    """遞迴脫敏 JSON 值。secret: 引用原樣保留。"""

    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and _SENSITIVE_KEY.search(key) and isinstance(item, str):
                redacted[key] = item if item.startswith("secret:") else REDACTED
            else:
                redacted[key] = redact_json(item)
        return redacted
    if isinstance(value, list):
        return [redact_json(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def redact_json_text(value: str) -> str:
    """能解析就按結構脫敏，否則按文本脫敏。"""

    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return redact_text(value)
    return json.dumps(redact_json(parsed), ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def contains_plaintext_secret(value: str) -> bool:
    """判斷文本是否仍含應脫敏的明文。"""

    if _SK.search(value) or _BEARER.search(value):
        return True
    for match in _ASSIGNED_SECRET.finditer(value):
        token = match.group(2).strip("\"'")
        if token.startswith("secret:") or token == REDACTED:
            continue
        return True
    return False
