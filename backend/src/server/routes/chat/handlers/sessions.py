"""会话查询端点 — 列表、消息、删除。"""

from typing import Any

from fastapi import HTTPException

from src.server.routes.chat.models import ChatSession, StoredChatMessage
from src.server.routes.chat.router import router
from src.server.routes.chat.store import _load_store, _save_store
from src.server.routes.chat.team_runtime.maintenance import (
    delete_team_runs_for_session,
    recover_team_snapshot,
)
from src.server.routes.chat.team_runtime.store import TeamStoreError


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
async def list_session_messages(session_id: str) -> list[dict[str, Any]]:
    """获取指定会话的消息列表"""
    store = _load_store()
    if session_id not in store.messages:
        raise HTTPException(status_code=404, detail="Session not found")
    return _normalize_history_messages(store.messages[session_id])


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(session_id: str) -> None:
    """删除指定会话及消息"""
    store = _load_store()
    if session_id not in store.messages:
        raise HTTPException(status_code=404, detail="Session not found")

    try:
        delete_team_runs_for_session(session_id)
    except TeamStoreError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    store.sessions = [session for session in store.sessions if session.id != session_id]
    store.messages.pop(session_id, None)
    _save_store(store)


def _normalize_history_messages(
    messages: list[StoredChatMessage],
) -> list[dict[str, Any]]:
    """读取历史消息，只返回 Chat 前端稳定消费字段。"""

    return [_history_message_to_response(message) for message in messages]


def _history_message_to_response(message: StoredChatMessage) -> dict[str, Any]:
    """把持久化消息转成前端历史回放契约。"""

    return {
        "id": message.id,
        "role": message.role,
        "content": message.content,
        "agent_id": message.agent_id,
        "agent_name": message.agent_name,
        "model_name": message.model_name,
        "error": message.error,
        "thinking": message.thinking,
        "images": [item.model_dump(mode="json", by_alias=True) for item in message.images],
        "displaySnapshot": message.display_snapshot,
        "teamSnapshot": recover_team_snapshot(message.team_snapshot),
        "clarificationRequests": message.clarification_requests,
        "clarificationResolved": message.clarification_resolved,
        "created_at": message.created_at,
    }
