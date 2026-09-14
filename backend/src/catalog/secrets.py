"""从 MCP/HTTP 定义中剥离与回填密钥。"""

from __future__ import annotations

import json
import re
from typing import Any

from src.config.secret_store import delete_named_secret, get_named_secret, set_named_secret

SENSITIVE_HEADER_NAMES = {
    "authorization",
    "proxy-authorization",
    "cookie",
    "x-api-key",
    "x-apikey",
    "x-auth-token",
}
SECRET_REF_RE = re.compile(r"^secret:[A-Za-z0-9:._-]{1,180}$")


def is_secret_ref(value: str) -> bool:
    return bool(SECRET_REF_RE.match(str(value or "").strip()))


def is_sensitive_header(name: str) -> bool:
    return str(name or "").strip().lower() in SENSITIVE_HEADER_NAMES


def mcp_header_ref(server_id: str, header_name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", header_name.strip()).strip("-") or "header"
    return f"secret:mcp:{server_id}:{slug}"


def http_auth_ref(tool_id: str) -> str:
    return f"secret:http:{tool_id}:auth"


def split_headers(headers: dict[str, str], *, server_id: str) -> tuple[dict[str, str], dict[str, str]]:
    """返回 (可写进 YAML 的 headers, header_refs)。"""

    public: dict[str, str] = {}
    refs: dict[str, str] = {}
    for key, value in dict(headers or {}).items():
        name = str(key).strip()
        text = str(value or "").strip()
        if not name or not text:
            continue
        if is_secret_ref(text):
            refs[name] = text
            continue
        if is_sensitive_header(name):
            ref = mcp_header_ref(server_id, name)
            set_named_secret(ref, text)
            refs[name] = ref
            continue
        public[name] = text
    return public, refs


def yaml_has_plaintext_secrets(payload: dict[str, Any]) -> bool:
    """文件里不得出现 Authorization 明文或 auth_config 对象。"""

    if "auth_config" in payload or "authConfig" in payload:
        return True
    headers = payload.get("headers") or {}
    if isinstance(headers, dict):
        for key, value in headers.items():
            if is_sensitive_header(str(key)) and not is_secret_ref(str(value or "")):
                return True
    return False


def resolve_headers(public: dict[str, str], refs: dict[str, str]) -> dict[str, str]:
    merged = dict(public or {})
    for name, ref in dict(refs or {}).items():
        secret = get_named_secret(ref) if is_secret_ref(ref) else ""
        if secret:
            merged[name] = secret
    return merged


def store_http_auth(tool_id: str, auth_config: dict[str, Any]) -> str:
    ref = http_auth_ref(tool_id)
    payload = {str(key): value for key, value in dict(auth_config or {}).items()}
    if not payload:
        delete_named_secret(ref)
        return ""
    set_named_secret(ref, json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return ref


def load_http_auth(ref: str) -> dict[str, Any]:
    if not is_secret_ref(ref):
        return {}
    raw = get_named_secret(ref)
    if not raw:
        return {}
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError:
        return {"token": raw}
    return loaded if isinstance(loaded, dict) else {}


def redact_headers(headers: dict[str, str]) -> dict[str, str]:
    return {
        str(key): value
        for key, value in dict(headers or {}).items()
        if not is_sensitive_header(str(key))
    }
