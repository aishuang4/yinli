"""群聊請求的 Session 授權上下文。

舊請求仍可帶專案欄位；一旦帶了 sessionId，就只認 owner Session。
"""

from __future__ import annotations

from contextvars import ContextVar

_REQUEST_SESSION: ContextVar[str] = ContextVar("group_studio_request_session", default="")


def bind_request_session(session_id: str):
    return _REQUEST_SESSION.set(session_id.strip())


def reset_request_session(token) -> None:
    _REQUEST_SESSION.reset(token)


def current_request_session() -> str:
    return _REQUEST_SESSION.get()
