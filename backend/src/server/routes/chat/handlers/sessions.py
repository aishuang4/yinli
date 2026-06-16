"""会话查询端点 — 列表、消息、删除。"""

from fastapi import HTTPException

from src.server.routes.chat.models import ChatSession, StoredChatMessage
from src.server.routes.chat.router import router
from src.server.routes.chat.store import _load_store, _save_store


@router.get("/sessions")
async def list_sessions() -> list[ChatSession]:
    """
    获取会话列表

    Returns:
        list[ChatSession]: 会话列表
    """
    store = _load_store()
    return sorted(store.sessions, key=lambda item: item.updated_at, reverse=True)


@router.get("/sessions/{session_id}/messages")
async def list_session_messages(session_id: str) -> list[StoredChatMessage]:
    """获取指定会话的消息列表"""
    store = _load_store()
    if session_id not in store.messages:
        raise HTTPException(status_code=404, detail="Session not found")
    return store.messages[session_id]


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(session_id: str) -> None:
    """删除指定会话及消息"""
    store = _load_store()
    if session_id not in store.messages:
        raise HTTPException(status_code=404, detail="Session not found")

    store.sessions = [session for session in store.sessions if session.id != session_id]
    store.messages.pop(session_id, None)
    _save_store(store)
