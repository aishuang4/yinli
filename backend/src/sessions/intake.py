"""新對話先有 Session。建立 Session 不會寫入 Project。"""

from __future__ import annotations

import re
import uuid

from src.sessions.errors import SessionError
from src.sessions.records import create_session, get_session

_SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,80}$")


def ensure_session_for_message(
    *,
    session_id: str | None,
    agent_id: str,
    title: str = "",
) -> str:
    """已有 Session 直接沿用。沒有則建立，且不寫入舊歸屬欄位。"""

    if session_id and get_session(session_id):
        existing = get_session(session_id)
        assert existing is not None
        return existing["id"]
    identifier = session_id if session_id and _SESSION_ID.fullmatch(session_id) else f"ses{uuid.uuid4().hex}"
    create_session(
        session_id=identifier,
        agent_id=agent_id or "default",
        title=title or "新對話",
        project_id="",
    )
    return identifier
