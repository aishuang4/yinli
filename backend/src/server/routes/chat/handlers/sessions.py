"""会话查询端点 — 列表、消息、删除。"""

from pathlib import Path
from typing import Annotated, Any

from fastapi import HTTPException, Query
from pydantic import BaseModel, Field

from src.project_files import delete_project_session_files
from src.server.routes.chat.models import ChatSession, StoredChatMessage
from src.server.routes.chat.router import router
from src.server.routes.chat.session_studio import (
    archive_studio_on_session_delete,
    attach_group_studio_hints,
)
from src.server.routes.chat.store import _load_store, _save_store
from src.server.routes.chat.team_runtime.maintenance import (
    delete_team_runs_for_session,
)
from src.server.routes.chat.team_runtime.store import TeamStoreError
from src.storage.database import DEFAULT_PROJECT_ID, get_project

_SESSION_KINDS = {"chat", "scheduled_job", "all"}


class SessionModelUpdate(BaseModel):
    """只保存模型配置身份，不保存连接凭据。"""
    model_config_id: str = Field(alias="modelConfigId", min_length=1)


@router.patch("/sessions/{session_id}/model")
async def update_session_model(session_id: str, data: SessionModelUpdate,
    project_id: str = Query(default=DEFAULT_PROJECT_ID, alias="projectId")) -> dict:
    from src.config.runtime_settings import resolve_model_binding
    from src.storage.database import list_chat_runs_by_statuses
    from src.server.routes.chat.lifecycle import ACTIVE_RUN_STATUSES
    if any(run["sessionId"] == session_id for run in list_chat_runs_by_statuses(ACTIVE_RUN_STATUSES)):
        raise HTTPException(status_code=409, detail="当前会话仍有运行或待审批任务。")
    store = _load_store()
    session = next((s for s in store.sessions if s.id == session_id and s.project_id == project_id), None)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    try:
        binding = resolve_model_binding(data.model_config_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    session.model_config_id = binding.primary.entry.id
    _save_store(store)
    return {"modelConfigId": session.model_config_id}


def _filter_sessions_by_kind(sessions: list[ChatSession], kind: str) -> list[ChatSession]:
    """默认隐藏 kind=scheduled_job；kind=all 仅调试。"""

    if kind not in _SESSION_KINDS:
        raise HTTPException(status_code=422, detail=f"非法 session kind: {kind}")
    if kind == "all":
        return list(sessions)
    return [session for session in sessions if session.kind == kind]


@router.get("/sessions")
async def list_sessions(
    project_id: str = Query(default=DEFAULT_PROJECT_ID, alias="projectId"),
    kind: Annotated[str, Query()] = "chat",
) -> list[ChatSession]:
    """
    获取会话列表

    Returns:
        list[ChatSession]: 会话列表
    """
    store = _load_store()
    matched = [
        session for session in store.sessions if session.project_id == project_id
    ]
    return attach_group_studio_hints(sorted(
        _filter_sessions_by_kind(matched, kind),
        key=lambda item: item.updated_at,
        reverse=True,
    ))


@router.get("/sessions/catalog")
async def list_session_catalog(
    kind: Annotated[str, Query()] = "chat",
) -> list[ChatSession]:
    """列出全部工作上下文中的会话，用于按 Workspace / Project 构建历史目录。"""

    store = _load_store()
    return attach_group_studio_hints(sorted(
        _filter_sessions_by_kind(store.sessions, kind),
        key=lambda item: item.updated_at,
        reverse=True,
    ))


@router.get("/sessions/{session_id}/messages")
async def list_session_messages(
    session_id: str,
    project_id: str = Query(default=DEFAULT_PROJECT_ID, alias="projectId"),
) -> list[dict[str, Any]]:
    """获取指定会话的消息列表"""
    store = _load_store()
    session = next(
        (item for item in store.sessions if item.id == session_id and item.project_id == project_id),
        None,
    )
    if session is None or session_id not in store.messages:
        raise HTTPException(status_code=404, detail="Session not found")
    return _normalize_history_messages(store.messages[session_id])


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(
    session_id: str,
    project_id: str = Query(default=DEFAULT_PROJECT_ID, alias="projectId"),
) -> None:
    """删除指定会话、消息及其私有附件和产物。"""
    store = _load_store()
    session = next(
        (item for item in store.sessions if item.id == session_id and item.project_id == project_id),
        None,
    )
    if session is None or session_id not in store.messages:
        raise HTTPException(status_code=404, detail="Session not found")

    project = get_project(session.project_id)
    if project is None:
        raise HTTPException(status_code=409, detail="会话所属 Project 不存在，无法清理私有文件")
    project_root = Path(str(project.get("rootPath") or "")).expanduser().resolve()
    if not project_root.is_dir():
        raise HTTPException(status_code=409, detail="会话所属 Project root 不可用，无法清理私有文件")

    try:
        archive_studio_on_session_delete(session_id)
        delete_team_runs_for_session(session_id)
        delete_project_session_files(project_root=project_root, session_id=session_id)
    except (OSError, ValueError, TeamStoreError) as exc:
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
        "files": [item.model_dump(mode="json", by_alias=True) for item in message.files],
        "displaySnapshot": message.display_snapshot,
        "clarificationRequests": message.clarification_requests,
        "clarificationResolved": message.clarification_resolved,
        "created_at": message.created_at,
    }
