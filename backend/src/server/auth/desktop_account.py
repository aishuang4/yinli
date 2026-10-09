"""桌面端保存在本机的托管登录令牌。不进入聊天记录。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.config.runtime_paths import USER_DATA_ROOT

ACCOUNT_FILE = USER_DATA_ROOT / "hosted-account.json"


def account_path() -> Path:
    return ACCOUNT_FILE


def load_desktop_account() -> dict[str, str] | None:
    path = account_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    token = str(data.get("token") or "")
    base_url = str(data.get("baseUrl") or "").rstrip("/")
    if not token or not base_url:
        return None
    return {"token": token, "baseUrl": base_url, "email": str(data.get("email") or "")}


def save_desktop_account(*, base_url: str, token: str, email: str) -> None:
    path = account_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"baseUrl": base_url.rstrip("/"), "token": token, "email": email}
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def clear_desktop_account() -> None:
    path = account_path()
    if path.is_file():
        path.unlink()


def public_desktop_account() -> dict[str, Any]:
    saved = load_desktop_account()
    if saved is None:
        return {"connected": False, "email": ""}
    return {"connected": True, "email": saved["email"], "baseUrl": saved["baseUrl"]}
