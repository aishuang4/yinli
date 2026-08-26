"""群聊工作室应用服务：from-chat 绑定、快照读取。不启动 LLM。"""

from __future__ import annotations

import uuid
from pathlib import Path

from src.group_studio.errors import (
    AGENT_NOT_BOUND,
    CHAT_RUN_ACTIVE,
    CROSS_PROJECT_SESSION,
    GROUP_STUDIO_NOT_FOUND,
    JOB_AGENT_FORBIDDEN,
    GroupStudioError,
)
from src.group_studio.models import (
    GROUP_STUDIO_HOST_AGENT_ID,
    GroupStudio,
    GroupStudioFromChatRequest,
)
from src.group_studio.settings import get_group_studio_settings_public
from src.project_resources import resolve_project_resource_access
from src.runtime_context import resolve_runtime_context
from src.server.routes.chat.lifecycle import ACTIVE_RUN_STATUSES
from src.server.routes.chat.store import (
    ChatSessionProjectConflict,
    _load_store,
    ensure_chat_session,
)
from src.storage.database import (
    archive_group_studio,
    create_group_studio_from_chat,
    get_active_group_studio_by_chat_session,
    get_group_studio_snapshot,
    list_agents,
    list_chat_runs_by_statuses,
    list_group_studio_messages,
)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _raise_if_chat_run_active(session_id: str) -> None:
    for run in list_chat_runs_by_statuses(ACTIVE_RUN_STATUSES):
        if str(run.get("sessionId") or "") == session_id:
            raise GroupStudioError(409, CHAT_RUN_ACTIVE)


def _resolve_worker(agent_id: str) -> dict[str, str]:
    if agent_id == "default":
        return {"id": "default", "name": "默认助手", "role": "chat"}
    for agent in list_agents():
        if str(agent.get("id") or "") != agent_id:
            continue
        role = str(agent.get("role") or "chat")
        if role == "job":
            raise GroupStudioError(403, JOB_AGENT_FORBIDDEN)
        return {
            "id": agent_id,
            "name": str(agent.get("name") or agent_id),
            "role": role,
        }
    raise GroupStudioError(403, AGENT_NOT_BOUND)


def _last_user_message(session_id: str) -> str:
    store = _load_store()
    messages = store.messages.get(session_id) or []
    for message in reversed(messages):
        if message.role == "user" and message.content.strip():
            return message.content.strip()
    return ""


def _message_count(session_id: str) -> int:
    store = _load_store()
    session = next((item for item in store.sessions if item.id == session_id), None)
    if session is not None:
        return int(session.message_count or 0)
    return len(store.messages.get(session_id) or [])


def _studio_workspace(project_root: Path, group_id: str) -> Path:
    return project_root / ".agent-workstation" / "group-studios" / group_id


def _ensure_workspace_dirs(project_root: Path, group_id: str) -> None:
    root = _studio_workspace(project_root, group_id)
    for name in ("inputs", "workspace", "outputs"):
        (root / name).mkdir(parents=True, exist_ok=True)


def _snapshot_to_model(raw: dict) -> GroupStudio:
    return GroupStudio.model_validate(raw)


def get_studio(group_id: str) -> GroupStudio:
    raw = get_group_studio_snapshot(group_id)
    if raw is None:
        raise GroupStudioError(404, GROUP_STUDIO_NOT_FOUND)
    return _snapshot_to_model(raw)


def get_studio_by_chat_session(session_id: str) -> GroupStudio | None:
    raw = get_active_group_studio_by_chat_session(session_id)
    if raw is None:
        return None
    return _snapshot_to_model(raw)


def list_studio_messages(
    group_id: str,
    *,
    before: str | None = None,
    limit: int = 100,
) -> list[dict]:
    if get_group_studio_snapshot(group_id, message_limit=1) is None:
        raise GroupStudioError(404, GROUP_STUDIO_NOT_FOUND)
    return list_group_studio_messages(group_id, before=before, limit=limit)


def from_chat(request: GroupStudioFromChatRequest) -> GroupStudio:
    """升级或回到当前 Chat 会话对应的未归档工作室。"""

    session_id = request.chat_session_id.strip()
    _raise_if_chat_run_active(session_id)

    existing = get_active_group_studio_by_chat_session(session_id)
    if existing is not None:
        if request.project_id and str(existing.get("projectId") or "") != request.project_id:
            raise GroupStudioError(422, CROSS_PROJECT_SESSION)
        return _snapshot_to_model(existing)

    worker = _resolve_worker(request.agent_id)
    try:
        context = resolve_runtime_context(
            project_id=request.project_id,
            session_id="",
            run_id="",
            agent_id=worker["id"],
        )
    except ValueError as exc:
        raise GroupStudioError(422, CROSS_PROJECT_SESSION) from exc

    try:
        access = resolve_project_resource_access(context)
        access.require_agent(worker["id"])
    except ValueError as exc:
        raise GroupStudioError(403, AGENT_NOT_BOUND) from exc

    try:
        ensure_chat_session(
            session_id=session_id,
            agent_id=worker["id"],
            agent_name=worker["name"],
            workspace_id=context.workspace_id,
            project_id=context.project_id,
            title=request.title or request.draft_text,
        )
    except ChatSessionProjectConflict as exc:
        raise GroupStudioError(422, CROSS_PROJECT_SESSION) from exc

    try:
        context = resolve_runtime_context(
            project_id=request.project_id,
            session_id=session_id,
            run_id="",
            agent_id=worker["id"],
        )
    except ValueError as exc:
        raise GroupStudioError(422, CROSS_PROJECT_SESSION) from exc

    message_count = _message_count(session_id)
    draft = request.draft_text.strip()
    last_user = _last_user_message(session_id) if message_count >= 1 else ""
    if message_count >= 1:
        goal = last_user
        seed = True
    else:
        goal = draft
        seed = False
    studio_status = "discussing" if goal else "drafting"
    card_status = "active" if goal else "draft"
    title = (request.title or "").strip() or (goal[:28] + ("..." if len(goal) > 28 else "") if goal else "未命名工作室")
    settings = get_group_studio_settings_public()
    group_id = _new_id("gstu")
    task_id = _new_id("gtask")
    host_id = _new_id("gmbr")
    worker_id = _new_id("gmbr")
    artifacts = []
    for path in request.file_paths:
        cleaned = str(path or "").strip()
        if not cleaned:
            continue
        artifacts.append({
            "id": _new_id("gart"),
            "path": cleaned,
            "title": Path(cleaned).name,
            "mediaType": "",
            "byteSize": 0,
            "createdBy": "human",
        })
    payload = {
        "id": group_id,
        "workspaceId": context.workspace_id,
        "projectId": context.project_id,
        "chatSessionId": session_id,
        "title": title,
        "status": studio_status,
        "budget": {
            "maxTurns": int(settings["maxTurns"]),
            "maxNoProgressTurns": 3,
            "usedTurns": 0,
            "noProgressTurns": 0,
        },
        "taskCard": {
            "id": task_id,
            "revision": 1,
            "status": card_status,
            "goal": goal,
            "background": "",
            "constraints": [],
            "dodChecklist": [],
            "seedFromChat": seed,
        },
        "blackboard": {
            "revision": 1,
            "goal": goal,
            "constraints": [],
            "decisions": [],
            "openQuestions": [],
            "artifacts": [item["id"] for item in artifacts],
        },
        "members": [
            {
                "id": host_id,
                "role": "host",
                "agentId": GROUP_STUDIO_HOST_AGENT_ID,
                "displayName": "主持人",
                "status": "active",
                "muted": False,
                "crashCount": 0,
            },
            {
                "id": worker_id,
                "role": "worker",
                "agentId": worker["id"],
                "displayName": worker["name"],
                "status": "active",
                "muted": False,
                "crashCount": 0,
            },
        ],
        "artifacts": artifacts,
    }
    snapshot = create_group_studio_from_chat(payload)
    _ensure_workspace_dirs(context.project_root, snapshot["id"])
    return _snapshot_to_model(snapshot)


def archive_studio_for_session_delete(group_id: str) -> None:
    """删除 Chat session 时归档工作室。"""

    archive_group_studio(group_id)
