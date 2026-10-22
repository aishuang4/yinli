"""会话持久化 — 按行追加用户/助手消息并维护会话摘要。"""

import uuid
from datetime import datetime, timezone

from src.storage.engine.database import DEFAULT_PROJECT_ID
from src.storage.records.chat_session_records import (
    append_chat_message,
    delete_chat_messages,
    get_chat_session,
    insert_chat_session_if_absent,
    list_chat_messages,
    update_chat_message_snapshot,
    update_chat_session,
)

from src.server.routes.chat.models import (
    ChatImagePayload,
    ChatFilePayload,
    ChatSession,
    StoredChatMessage,
)


def session_model(record: dict | None) -> ChatSession | None:
    """把增量读取的会话行收成 API 模型。"""

    if record is None:
        return None
    return ChatSession.model_validate(record)


def message_model(record: dict) -> StoredChatMessage:
    """把增量读取的消息行收成持久化模型。"""

    return StoredChatMessage.model_validate(record)


def _append_user_message(
    *,
    session_id: str,
    agent_id: str,
    agent_name: str,
    mode: str,
    model_name: str,
    content: str,
    images: list[ChatImagePayload],
    files: list[ChatFilePayload] | None = None,
    project_slug: str = DEFAULT_PROJECT_ID,
    cwd: str = "",
    approval_mode: str | None = None,
) -> None:
    """追加用户消息，并创建或更新会话摘要"""
    now = datetime.now(timezone.utc).isoformat()
    from src.config.runtime_settings import current_model_binding
    binding = current_model_binding()
    model_config_id = binding.primary.entry.id if binding else ""

    existing = get_chat_session(session_id)
    if existing is None:
        insert_chat_session_if_absent({
            "id": session_id,
            "title": _make_session_title(content),
            "agent_id": agent_id,
            "agent_name": agent_name,
            "mode": mode,
            "project_slug": project_slug,
            "cwd": cwd,
            "created_at": now,
            "updated_at": now,
            "modelConfigId": model_config_id,
            **({"approvalMode": approval_mode} if approval_mode else {}),
        })
    else:
        update_chat_session(
            session_id,
            agent_id=agent_id,
            agent_name=agent_name,
            mode=mode,
            project_slug=project_slug,
            cwd=cwd,
            model_config_id=model_config_id or existing.get("modelConfigId") or "",
            updated_at=now,
        )
    append_chat_message(session_id, {
        "id": uuid.uuid4().hex,
        "role": "user",
        "content": content,
        "agent_id": agent_id,
        "agent_name": agent_name,
        "model_name": model_name,
        "images": [item.model_dump(mode="json") for item in images],
        "files": [item.model_dump(mode="json") for item in (files or [])],
        "created_at": now,
    })


class ChatSessionProjectConflict(ValueError):
    """已有 Chat session 属于另一个 Project。"""

    def __init__(self, session_id: str, existing_project_id: str, requested_project_id: str) -> None:
        super().__init__(
            f"Session {session_id} 属于 Project {existing_project_id}，"
            f"不能在 Project {requested_project_id} 中继续。"
        )
        self.session_id = session_id
        self.existing_project_id = existing_project_id
        self.requested_project_id = requested_project_id


def ensure_chat_session(
    *,
    session_id: str,
    agent_id: str,
    agent_name: str,
    project_slug: str,
    cwd: str = "",
    title: str = "",
) -> ChatSession:
    """经 ChatStore 确保 kind=chat 的 session 存在；禁止 group_studio_records 直接 SQL INSERT。

    send 与 from-chat 共用。已有 session 若 project 不一致则抛 ChatSessionProjectConflict。
    """

    now = datetime.now(timezone.utc).isoformat()
    record = insert_chat_session_if_absent({
        "id": session_id,
        "title": _make_session_title(title) if title.strip() else "新对话",
        "agent_id": agent_id,
        "agent_name": agent_name,
        "mode": "agent",
        "project_slug": project_slug,
        "cwd": cwd,
        "kind": "chat",
        "created_at": now,
        "updated_at": now,
    })
    if record["project_slug"] != project_slug:
        raise ChatSessionProjectConflict(session_id, record["project_slug"], project_slug)
    return session_model(record)


def ensure_scheduled_job_session(
    *,
    session_id: str,
    agent_id: str,
    agent_name: str,
    project_slug: str,
    cwd: str = "",
    title: str,
) -> ChatSession:
    """经 ChatStore 创建 kind=scheduled_job 的新 session，禁止直接 SQL INSERT。"""

    now = datetime.now(timezone.utc).isoformat()
    record = insert_chat_session_if_absent({
        "id": session_id,
        "title": _make_session_title(title) if title.strip() else "定时任务",
        "agent_id": agent_id,
        "agent_name": agent_name,
        "mode": "agent",
        "project_slug": project_slug,
        "cwd": cwd,
        "kind": "scheduled_job",
        "created_at": now,
        "updated_at": now,
    })
    return session_model(record)


def _append_assistant_message(
    *,
    session_id: str,
    agent_id: str,
    agent_name: str,
    model_name: str,
    content: str,
    error: str | None,
    thinking: str = "",
    skill_calls: list | None = None,
    tool_calls: list | None = None,
    model_caches: list | None = None,
    runtime_events: list[dict] | None = None,
    plan_runs: list | None = None,
    display_snapshot: dict | None = None,
    clarification_requests: list[dict] | None = None,
    clarification_resolved: bool = False,
) -> None:
    """追加助手消息，并更新会话摘要"""
    now = datetime.now(timezone.utc).isoformat()
    append_chat_message(session_id, {
        "id": uuid.uuid4().hex,
        "role": "assistant",
        "content": content,
        "agent_id": agent_id,
        "agent_name": agent_name,
        "model_name": model_name,
        "error": error,
        "thinking": thinking,
        "skill_calls": skill_calls or [],
        "tool_calls": tool_calls or [],
        "model_caches": model_caches or [],
        "runtime_events": runtime_events or [],
        "plan_runs": plan_runs or [],
        "displaySnapshot": display_snapshot or {
            "version": 4,
            "lastSeq": 0,
            "streamDone": False,
            "order": [],
            "blocksById": {},
        },
        "clarification_requests": clarification_requests or [],
        "clarification_resolved": clarification_resolved,
        "created_at": now,
    })
    update_chat_session(session_id, updated_at=now)


def _update_latest_assistant_display_snapshot(
    *,
    session_id: str,
    display_snapshot: dict,
    min_created_at: str = "",
) -> None:
    """把 V4 Snapshot 写回最新消息，并移除同一 Run 的旧等待占位消息。"""

    messages = list_chat_messages(session_id)
    target: dict | None = None
    for message in reversed(messages):
        if message.get("role") != "assistant":
            continue
        if min_created_at and str(message.get("created_at") or "") < min_created_at:
            return
        target = message
        break
    if target is None:
        return

    update_chat_message_snapshot(str(target["id"]), display_snapshot)
    root_ids = {
        str(block_id)
        for block_id, block in (display_snapshot.get("blocksById") or {}).items()
        if isinstance(block, dict) and block.get("kind") == "message"
    }
    if not root_ids:
        return
    duplicate_ids = [
        str(message["id"])
        for message in messages
        if message["id"] != target["id"]
        and message.get("role") == "assistant"
        and root_ids.intersection(((message.get("displaySnapshot") or {}).get("blocksById") or {}).keys())
    ]
    delete_chat_messages(session_id, duplicate_ids)


def _make_session_title(content: str) -> str:
    """用首条用户消息生成会话标题"""
    title = " ".join(content.strip().split())
    if not title:
        return "新对话"
    return title[:28] + ("..." if len(title) > 28 else "")
