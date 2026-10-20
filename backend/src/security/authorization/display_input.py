"""工具参数展示脱敏；保留普通业务输入，不改变用于执行或授权的原参数。"""

import json
import re
from typing import Any

_CREDENTIAL_FIELD = re.compile(
    r"(?i)^(?:authorization|proxy.?authorization|cookie|set.cookie|password|passwd|"
    r"(?:x.?)?api.?key|secret|client.?secret|access.?token|refresh.?token|token|credential|private.?key)$"
)
_CREDENTIAL_TEXT = re.compile(
    r"(?i)(?:\b(?:Bearer|Basic)\s+[^\s\"']+|\b(?:sk[-_]|k2a_)[A-Za-z0-9_-]{8,}|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----|"
    r"(?:password|api[_-]?key|secret|access[_-]?token|refresh[_-]?token|token)\s*[:=]\s*[^\s,;\"']+|"
    r"https?://[^/\s]*@)", re.DOTALL,
)
HIDDEN_CREDENTIAL = "[凭据已隐藏]"


def display_input(value: Any, *, secrets: tuple[str, ...] = ()) -> Any:
    """凭据字段、文本内凭据及已配置密钥脱敏，其他字符串/数字/布尔原样保留。"""
    if isinstance(value, dict):
        return {key: HIDDEN_CREDENTIAL if _CREDENTIAL_FIELD.fullmatch(str(key)) else display_input(item, secrets=secrets)
                for key, item in value.items()}
    if isinstance(value, list):
        return [display_input(item, secrets=secrets) for item in value]
    if isinstance(value, str):
        for secret in sorted((item for item in secrets if item), key=len, reverse=True):
            value = value.replace(secret, HIDDEN_CREDENTIAL)
        return _CREDENTIAL_TEXT.sub(HIDDEN_CREDENTIAL, value)
    return value


def display_call_input(raw: Any, *, secrets: tuple[str, ...] = ()) -> str:
    """只展示完整 JSON；解析失败不把可能切断的凭据片段直接写入历史。"""
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
        return json.dumps(display_input(value, secrets=secrets), ensure_ascii=False)
    except (ValueError, TypeError):
        return json.dumps({"说明": "工具参数不是完整 JSON，暂无法展示"}, ensure_ascii=False)
