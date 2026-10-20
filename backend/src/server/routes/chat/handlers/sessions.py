"""会话查询端点 — 列表、消息、删除。"""

from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import HTTPException, Query
from pydantic import BaseModel, Field

from src.sessions.files import delete_project_session_files
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
    delete_chat_session,
    delete_session_context_state,
    get_chat_run,
    get_chat_session,
    list_chat_messages,
    list_chat_sessions,
    update_chat_session,
)

def _session_in_scope(session: ChatSession | None, project_id: str) -> bool:
    """Session 本身即範圍。projectId 不參與過濾。"""

    del project_id
    return session is not None


_SESSION_KINDS = {"chat", "scheduled_job", "all"}


class SessionApprovalUpdate(BaseModel):
    """下一轮选择独立于当前 Run，无等待中的工具批准副作用。"""
    approval_mode: Literal["manual", "model_review", "full_access"] = Field(alias="approvalMode")
    model_config = {"extra": "forbid"}


@router.patch("/sessions/{session_id}/approval-mode")
async def update_session_approval_mode(session_id: str, data: SessionApprovalUpdate,
    project_id: str = Query(default="", alias="projectId")) -> dict:
    """会话可以保存下一轮模式；当前 Run 的不可变选择与 pending 保持独立。"""
    session = session_model(get_chat_session(session_id))
    if not _session_in_scope(session, project_id):
        raise HTTPException(status_code=404, detail="Session not found")
    if data.approval_mode == "model_review":
        from src.security.authorization.review_gate import validated_review_binding
        try:
            validated_review_binding()
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
    update_chat_session(session_id, approval_mode=data.approval_mode)
    return {"approvalMode": data.approval_mode}


class AuthorizationReviewRequest(BaseModel):
    """只引用原 decision，不接受替换后的工具参数。"""

    decision_id: str = Field(alias="decisionId", min_length=1)
    model_config = {"extra": "forbid", "populate_by_name": True}


@router.post("/sessions/{session_id}/authorization-reviews")
async def request_authorization_review(session_id: str, data: AuthorizationReviewRequest,
    project_id: str = Query(default="", alias="projectId")) -> dict:
    """模型拒绝的人工复核创建新尝试，不重放已终止调用。"""

    session = session_model(get_chat_session(session_id))
    if not _session_in_scope(session, project_id):
        raise HTTPException(status_code=404, detail="Session not found")
    from src.storage.permission_records import PermissionRecordStore

    try:
        attempt, decision = PermissionRecordStore().request_human_review(data.decision_id, session_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return {
        "decisionId": decision.id,
        "actionFingerprint": attempt.fingerprint,
        "verdict": decision.verdict,
        "reason": decision.reason,
        "toolName": attempt.tool_name,
        "targets": [target.target for target in attempt.targets],
    }


class SessionModelUpdate(BaseModel):
    """只保存模型配置身份，不保存连接凭据。"""
    model_config_id: str = Field(alias="modelConfigId", min_length=1)


@router.patch("/sessions/{session_id}/model")
async def update_session_model(session_id: str, data: SessionModelUpdate,
    project_id: str = Query(default="", alias="projectId")) -> dict:
    from src.config.runtime_settings import resolve_model_binding
    from src.storage.database import list_chat_runs_by_statuses
    from src.server.routes.chat.lifecycle import ACTIVE_RUN_STATUSES
    if any(run["sessionId"] == session_id for run in list_chat_runs_by_statuses(ACTIVE_RUN_STATUSES)):
        raise HTTPException(status_code=409, detail="当前会话仍有运行或待审批任务。")
    record = get_chat_session(session_id)
    session = session_model(record)
    if not _session_in_scope(session, project_id):
        raise HTTPException(status_code=404, detail="Session not found")
    try:
        binding = resolve_model_binding(data.model_config_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    update_chat_session(session_id, model_config_id=binding.primary.entry.id)
    return {"modelConfigId": binding.primary.entry.id}


def _filter_sessions_by_kind(sessions: list[ChatSession], kind: str) -> list[ChatSession]:
    """默认隐藏 kind=scheduled_job；kind=all 仅调试。"""

    if kind not in _SESSION_KINDS:
        raise HTTPException(status_code=422, detail=f"非法 session kind: {kind}")
    if kind == "all":
        return list(sessions)
    return [session for session in sessions if session.kind == kind]


@router.get("/sessions")
async def list_sessions(
    project_id: str = Query(default="", alias="projectId"),
    kind: Annotated[str, Query()] = "chat",
) -> list[ChatSession]:
    """
    获取会话列表

    Returns:
        list[ChatSession]: 会话列表
    """
    del project_id
    records = list_chat_sessions(project_id=None, kind=None if kind == "all" else kind)
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
    project_id: str = Query(default="", alias="projectId"),
) -> list[dict[str, Any]]:
    """获取指定会话的消息列表"""
    session = session_model(get_chat_session(session_id))
    if not _session_in_scope(session, project_id):
        raise HTTPException(status_code=404, detail="Session not found")
    return _normalize_history_messages([
        message_model(item) for item in list_chat_messages(session_id)
    ])


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(
    session_id: str,
    project_id: str = Query(default="", alias="projectId"),
) -> None:
    """删除指定会话、消息及其私有附件和产物。"""
    session = session_model(get_chat_session(session_id))
    if not _session_in_scope(session, project_id):
        raise HTTPException(status_code=404, detail="Session not found")

    try:
        archive_studio_on_session_delete(session_id)
        delete_team_runs_for_session(session_id)
        delete_project_session_files(project_root=Path("."), session_id=session_id)
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
