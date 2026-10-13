"""桌面端保存在本机的托管登录资料。令牌不在这个文件里。"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def account_path() -> Path:
    from src.config import runtime_paths

    return runtime_paths.USER_DATA_ROOT / "hosted-account.json"


def load_desktop_account() -> dict[str, str] | None:
    from src.server.auth.cloud_client import load_account

    account = load_account()
    if account is None or not account.token:
        return None
    return {"token": account.token, "baseUrl": account.base_url, "email": account.email}


def save_desktop_account(*, base_url: str, token: str, email: str) -> None:
    from src.server.auth.cloud_client import save_account

    save_account(base_url, email, token)


def clear_desktop_account() -> None:
    from src.server.auth.cloud_client import clear_account

    clear_account()


def public_desktop_account() -> dict[str, Any]:
    from src.server.auth.cloud_client import public_account

    return public_account()
