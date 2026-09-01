"""Chat catalog / 删除会话时附加工作室信息。不经过 Team Runtime。"""

from __future__ import annotations

from src.group_studio.lifecycle import archive_for_chat_session_delete
from src.server.routes.chat.models import ChatSession
from src.storage.database import lookup_active_group_studios_by_chat_session_ids


def attach_group_studio_hints(sessions: list[ChatSession]) -> list[ChatSession]:
    """给 catalog 会话补上未归档工作室摘要。"""

    hints = lookup_active_group_studios_by_chat_session_ids([session.id for session in sessions])
    attached: list[ChatSession] = []
    for session in sessions:
        hint = hints.get(session.id)
        attached.append(session.model_copy(update={"group_studio": hint or None}))
    return attached


def archive_studio_on_session_delete(session_id: str) -> None:
    """删除 Chat session 时归档绑定工作室。"""

    archive_for_chat_session_delete(session_id)
