"""会话查询端点 — 列表、消息、删除。"""

from fastapi import HTTPException

from src.server.routes.chat.models import ChatSession, StoredChatMessage
from src.server.routes.chat.router import router
from src.server.routes.chat.store import _load_store, _save_store
from src.storage.database import list_run_events


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
    return _normalize_history_messages(session_id, store.messages[session_id])


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(session_id: str) -> None:
    """删除指定会话及消息"""
    store = _load_store()
    if session_id not in store.messages:
        raise HTTPException(status_code=404, detail="Session not found")

    store.sessions = [session for session in store.sessions if session.id != session_id]
    store.messages.pop(session_id, None)
    _save_store(store)


def _normalize_history_messages(
    session_id: str,
    messages: list[StoredChatMessage],
) -> list[StoredChatMessage]:
    """读取历史时补齐旧 clarification 记录，并隐藏内部续写 prompt。"""

    normalized = [_normalize_legacy_clarification_answer(message) for message in messages]
    existing_request_ids = {
        str(request.get("id") or "")
        for message in normalized
        for request in message.clarification_requests
        if isinstance(request, dict)
    }
    synthetic_messages: list[StoredChatMessage] = []
    for event in list_run_events(session_id):
        if event.get("eventType") != "CLARIFICATION_REQUIRED":
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict) or not payload.get("questions"):
            continue
        request_id = str(payload.get("id") or "")
        if request_id and request_id in existing_request_ids:
            continue
        synthetic_messages.append(StoredChatMessage(
            id=f"clarification-{event.get('id')}",
            role="assistant",
            content="",
            agent_id=event.get("agentId") or None,
            agent_name=event.get("agentName") or None,
            model_name=None,
            error=None,
            clarification_requests=[payload],
            clarification_resolved=True,
            created_at=str(event.get("createdAt") or ""),
        ))
        if request_id:
            existing_request_ids.add(request_id)

    if not synthetic_messages:
        return normalized
    return sorted(
        [*normalized, *synthetic_messages],
        key=lambda item: item.created_at or "",
    )


def _normalize_legacy_clarification_answer(message: StoredChatMessage) -> StoredChatMessage:
    """把旧版“针对上一个补充信息请求...”用户消息转成可读答案。"""

    prefix = "针对上一个补充信息请求，用户回答如下："
    if message.role != "user" or not message.content.startswith(prefix):
        return message
    answers: list[str] = []
    for line in message.content[len(prefix):].splitlines():
        text = line.strip()
        if not text.startswith("- "):
            continue
        _, _, answer = text[2:].partition(":")
        answer = answer.strip()
        if answer:
            answers.append(answer)
    return message.model_copy(update={"content": "；".join(answers) or "已补充信息"})
