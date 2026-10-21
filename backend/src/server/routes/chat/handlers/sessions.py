"""会话查询端点 — 列表、消息、删除。"""

from datetime import datetime
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
from src.server.routes.chat.store import message_model, session_model
from src.server.routes.chat.team_runtime.maintenance import (
    delete_team_runs_for_session,
)
from src.server.routes.chat.team_runtime.store import TeamStoreError
from src.storage.database import (
    DEFAULT_PROJECT_ID,
    delete_chat_session,
    delete_session_context_state,
    get_chat_run,
    get_chat_session,
    get_project,
    list_chat_messages,
    list_chat_sessions,
    update_chat_session,
)

_SESSION_KINDS = {"chat", "scheduled_job", "all"}


class SessionModelUpdate(BaseModel):
    """只保存模型配置身份，不保存连接凭据。"""
    model_config_id: str = Field(alias="modelConfigId", min_length=1)


class SessionApprovalUpdate(BaseModel):
    """下一轮审批模式。当前 Run 不因这次保存改写。"""

    approval_mode: str = Field(alias="approvalMode", min_length=1)


@router.patch("/sessions/{session_id}/model")
async def update_session_model(session_id: str, data: SessionModelUpdate,
    project_slug: str = Query(default=DEFAULT_PROJECT_ID, alias="projectSlug")) -> dict:
    from src.config.runtime_settings import resolve_model_binding
    from src.storage.database import list_chat_runs_by_statuses
    from src.server.routes.chat.lifecycle import ACTIVE_RUN_STATUSES
    if any(run["sessionId"] == session_id for run in list_chat_runs_by_statuses(ACTIVE_RUN_STATUSES)):
        raise HTTPException(status_code=409, detail="当前会话仍有运行或待审批任务。")
    record = get_chat_session(session_id)
    session = session_model(record)
    if session is None or session.project_slug != project_slug:
        raise HTTPException(status_code=404, detail="Session not found")
    try:
        binding = resolve_model_binding(data.model_config_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    update_chat_session(session_id, model_config_id=binding.primary.entry.id)
    return {"modelConfigId": binding.primary.entry.id}


@router.patch("/sessions/{session_id}/approval")
async def update_session_approval(session_id: str, data: SessionApprovalUpdate,
    project_slug: str = Query(default=DEFAULT_PROJECT_ID, alias="projectSlug")) -> dict:
    """保存下一轮模式。model_review 不可用时拒绝，不静默改成其他模式。"""

    from src.security.authorization.models import validate_approval_mode
    from src.security.authorization.review_gate import permission_review_status

    record = get_chat_session(session_id)
    session = session_model(record)
    if session is None or session.project_slug != project_slug:
        raise HTTPException(status_code=404, detail="Session not found")
    try:
        mode = validate_approval_mode(data.approval_mode)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    if mode == "model_review":
        review = permission_review_status()
        if not review["available"]:
            raise HTTPException(status_code=422, detail=review["reason"])
    update_chat_session(session_id, approval_mode=mode)
    return {"approvalMode": mode, "appliesTo": "next_run"}


def _filter_sessions_by_kind(sessions: list[ChatSession], kind: str) -> list[ChatSession]:
    """默认隐藏 kind=scheduled_job；kind=all 仅调试。"""

    if kind not in _SESSION_KINDS:
        raise HTTPException(status_code=422, detail=f"非法 session kind: {kind}")
    if kind == "all":
        return list(sessions)
    return [session for session in sessions if session.kind == kind]


@router.get("/sessions")
async def list_sessions(
    project_slug: str = Query(default=DEFAULT_PROJECT_ID, alias="projectSlug"),
    kind: Annotated[str, Query()] = "chat",
) -> list[ChatSession]:
    """
    获取会话列表

    Returns:
        list[ChatSession]: 会话列表
    """
    records = list_chat_sessions(project_slug=project_slug, kind=None if kind == "all" else kind)
    return attach_group_studio_hints(_filter_sessions_by_kind(
        [session for item in records if (session := session_model(item)) is not None],
        kind,
    ))


@router.get("/sessions/catalog")
async def list_session_catalog(
    kind: Annotated[str, Query()] = "chat",
) -> list[ChatSession]:
    """列出全部工作上下文中的会话，用于按 Workspace / Project 构建历史目录。"""

    records = list_chat_sessions(kind=None if kind == "all" else kind)
    return attach_group_studio_hints(_filter_sessions_by_kind(
        [session for item in records if (session := session_model(item)) is not None],
        kind,
    ))


@router.get("/sessions/{session_id}/messages")
async def list_session_messages(
    session_id: str,
    project_slug: str = Query(default=DEFAULT_PROJECT_ID, alias="projectSlug"),
) -> list[dict[str, Any]]:
    """获取指定会话的消息列表"""
    session = session_model(get_chat_session(session_id))
    if session is None or session.project_slug != project_slug:
        raise HTTPException(status_code=404, detail="Session not found")
    return _normalize_history_messages([
        message_model(item) for item in list_chat_messages(session_id)
    ])


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(
    session_id: str,
    project_slug: str = Query(default=DEFAULT_PROJECT_ID, alias="projectSlug"),
) -> None:
    """删除指定会话、消息及其私有附件和产物。"""
    session = session_model(get_chat_session(session_id))
    if session is None or session.project_slug != project_slug:
        raise HTTPException(status_code=404, detail="Session not found")

    project = get_project(session.project_slug)
    if project is None:
        raise HTTPException(status_code=409, detail="会话所属 Project 不存在，无法清理私有文件")
    project_root = Path(str(project.get("primaryPath") or "")).expanduser().resolve()
    if not project_root.is_dir():
        raise HTTPException(status_code=409, detail="会话所属 Project root 不可用，无法清理私有文件")

    try:
        archive_studio_on_session_delete(session_id)
        delete_team_runs_for_session(session_id)
        delete_project_session_files(project_root=project_root, session_id=session_id)
    except (OSError, ValueError, TeamStoreError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    delete_chat_session(session_id)
    # 压缩摘要 / 已启用 Skill 等会话级上下文状态一并清理。
    delete_session_context_state(session_id)


def _normalize_history_messages(
    messages: list[StoredChatMessage],
) -> list[dict[str, Any]]:
    """读取历史消息，只返回 Chat 前端稳定消费字段。"""

    return [_history_message_to_response(message) for message in messages]


def message_elapsed_seconds(message: StoredChatMessage) -> int | None:
    """用 Chat Run 的起止时间给出任务耗时，没有完整时间时留空。"""

    run_id = _message_run_id(message)
    if not run_id:
        return None
    run = get_chat_run(run_id)
    if not run:
        return None
    return elapsed_seconds_between(str(run.get("createdAt") or ""), str(run.get("completedAt") or ""))


def elapsed_seconds_between(start: str, end: str) -> int | None:
    """把两段 ISO 时间收成至少 1 秒的整数。"""

    if not start or not end:
        return None
    try:
        started = datetime.fromisoformat(start)
        finished = datetime.fromisoformat(end)
    except ValueError:
        return None
    seconds = int(round((finished - started).total_seconds()))
    if seconds < 0:
        return None
    return max(1, seconds)


def _message_run_id(message: StoredChatMessage) -> str:
    blocks = (message.display_snapshot or {}).get("blocksById") or {}
    if not isinstance(blocks, dict):
        return ""
    for block in blocks.values():
        if not isinstance(block, dict) or block.get("kind") != "message":
            continue
        block_id = str(block.get("id") or "")
        if block_id.startswith("message:"):
            return block_id.split(":", 1)[1]
    return ""


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
        "elapsedSeconds": message_elapsed_seconds(message),
        "created_at": message.created_at,
    }
