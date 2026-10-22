"""Chat session 的公开读写。

群聊升级会话时走这里，不要 import store._load_store。
"""
from __future__ import annotations


from src.server.routes.chat.lifecycle import ACTIVE_RUN_STATUSES
from src.server.routes.chat.models import ChatSession
from src.server.routes.chat.store import (
    ChatSessionProjectConflict,
    ensure_chat_session,
    ensure_scheduled_job_session,
)
from src.server.routes.chat.store import session_model
from src.storage.records.chat_session_records import get_chat_session

__all__ = [
    "ACTIVE_RUN_STATUSES",
    "ChatSessionProjectConflict",
    "ensure_chat_session",
    "ensure_scheduled_job_session",
    "find_chat_session",
]


def find_chat_session(session_id: str) -> ChatSession | None:
    """按 id 查找 Chat session。不存在时返回 None。"""

    return session_model(get_chat_session(session_id))
