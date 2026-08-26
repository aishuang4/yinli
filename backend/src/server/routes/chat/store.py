"""会话持久化 — 读写 ChatStore，追加用户/助手消息并维护会话摘要。"""

import uuid
from datetime import datetime, timezone

from src.storage.database import (
    DEFAULT_PROJECT_ID,
    DEFAULT_WORKSPACE_ID,
    get_json_record,
    load_chat_store,
    migrate_json_object_file,
    save_chat_store,
)

from src.server.routes.chat.models import (
    CHAT_DATA_PATH,
    CHAT_NAMESPACE,
    CHAT_STORE_KEY,
    ChatImagePayload,
    ChatFilePayload,
    ChatSession,
    ChatStore,
    StoredChatMessage,
)


def _load_store() -> ChatStore:
    """读取对话存储文件"""
    migrate_json_object_file(CHAT_NAMESPACE, CHAT_STORE_KEY, CHAT_DATA_PATH)
    data = load_chat_store()
    if not data.get("sessions") and not data.get("messages"):
        legacy = get_json_record(CHAT_NAMESPACE, CHAT_STORE_KEY)
        if legacy:
            save_chat_store(legacy)
            data = load_chat_store()
    if not data:
        return ChatStore()

    try:
        return ChatStore.model_validate(data)
    except Exception:
        return ChatStore()


def _save_store(store: ChatStore) -> None:
    """写入对话存储文件"""
    save_chat_store(store.model_dump(mode="json", by_alias=True))


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
    workspace_id: str = DEFAULT_WORKSPACE_ID,
    project_id: str = DEFAULT_PROJECT_ID,
) -> None:
    """追加用户消息，并创建或更新会话摘要"""
    now = datetime.now(timezone.utc).isoformat()
    store = _load_store()
    messages = store.messages.setdefault(session_id, [])
    messages.append(StoredChatMessage(
        id=uuid.uuid4().hex,
        role="user",
        content=content,
        agent_id=agent_id,
        agent_name=agent_name,
        model_name=model_name,
        images=images,
        files=files or [],
        created_at=now,
    ))

    title = _make_session_title(content)
    existing = next((session for session in store.sessions if session.id == session_id), None)
    if existing:
        existing.agent_id = agent_id
        existing.agent_name = agent_name
        existing.mode = mode
        existing.workspace_id = workspace_id
        existing.project_id = project_id
        existing.updated_at = now
        existing.message_count = len(messages)
    else:
        store.sessions.append(ChatSession(
            id=session_id,
            title=title,
            agent_id=agent_id,
            agent_name=agent_name,
            mode=mode,
            workspace_id=workspace_id,
            project_id=project_id,
            created_at=now,
            updated_at=now,
            message_count=len(messages),
        ))

    _save_store(store)


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
    workspace_id: str,
    project_id: str,
    title: str = "",
) -> ChatSession:
    """经 ChatStore 确保 kind=chat 的 session 存在；禁止 group_studio_records 直接 SQL INSERT。

    send 与 from-chat 共用。已有 session 若 project 不一致则抛 ChatSessionProjectConflict。
    """

    now = datetime.now(timezone.utc).isoformat()
    store = _load_store()
    existing = next((session for session in store.sessions if session.id == session_id), None)
    if existing is not None:
        if existing.project_id != project_id:
            raise ChatSessionProjectConflict(session_id, existing.project_id, project_id)
        return existing
    session = ChatSession(
        id=session_id,
        title=_make_session_title(title) if title.strip() else "新对话",
        agent_id=agent_id,
        agent_name=agent_name,
        mode="agent",
        workspace_id=workspace_id,
        project_id=project_id,
        kind="chat",
        created_at=now,
        updated_at=now,
        message_count=0,
    )
    store.sessions.append(session)
    _save_store(store)
    return session


def ensure_scheduled_job_session(
    *,
    session_id: str,
    agent_id: str,
    agent_name: str,
    workspace_id: str,
    project_id: str,
    title: str,
) -> ChatSession:
    """经 ChatStore 创建 kind=scheduled_job 的新 session，禁止直接 SQL INSERT。"""

    now = datetime.now(timezone.utc).isoformat()
    store = _load_store()
    existing = next((session for session in store.sessions if session.id == session_id), None)
    if existing is not None:
        return existing
    session = ChatSession(
        id=session_id,
        title=_make_session_title(title) if title.strip() else "定时任务",
        agent_id=agent_id,
        agent_name=agent_name,
        mode="agent",
        workspace_id=workspace_id,
        project_id=project_id,
        kind="scheduled_job",
        created_at=now,
        updated_at=now,
        message_count=0,
    )
    store.sessions.append(session)
    _save_store(store)
    return session


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
    store = _load_store()
    messages = store.messages.setdefault(session_id, [])
    _ = skill_calls, tool_calls, model_caches, runtime_events, plan_runs
    messages.append(StoredChatMessage(
        id=uuid.uuid4().hex,
        role="assistant",
        content=content,
        agent_id=agent_id,
        agent_name=agent_name,
        model_name=model_name,
        error=error,
        thinking=thinking,
        tool_calls=[],
        skill_calls=[],
        model_caches=[],
        runtime_events=[],
        plan_runs=[],
        display_snapshot=display_snapshot or {
            "version": 4,
            "lastSeq": 0,
            "streamDone": False,
            "order": [],
            "blocksById": {},
        },
        clarification_requests=clarification_requests or [],
        clarification_resolved=clarification_resolved,
        created_at=now,
    ))

    existing = next((session for session in store.sessions if session.id == session_id), None)
    if existing:
        existing.updated_at = now
        existing.message_count = len(messages)

    _save_store(store)


def _update_latest_assistant_display_snapshot(
    *,
    session_id: str,
    display_snapshot: dict,
    min_created_at: str = "",
) -> None:
    """把 V4 Snapshot 写回最新消息，并移除同一 Run 的旧等待占位消息。"""

    store = _load_store()
    messages = store.messages.get(session_id) or []
    target_index: int | None = None
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if message.role != "assistant":
            continue
        if min_created_at and message.created_at < min_created_at:
            return
        target_index = index
        break
    if target_index is None:
        return

    messages[target_index].display_snapshot = display_snapshot
    root_ids = {
        str(block_id)
        for block_id, block in (display_snapshot.get("blocksById") or {}).items()
        if isinstance(block, dict) and block.get("kind") == "message"
    }
    if root_ids:
        duplicate_indices = {
            index
            for index, message in enumerate(messages)
            if index != target_index
            and message.role == "assistant"
            and root_ids.intersection(((message.display_snapshot or {}).get("blocksById") or {}).keys())
        }
        if duplicate_indices:
            messages[:] = [
                message
                for index, message in enumerate(messages)
                if index not in duplicate_indices
            ]
            session = next((item for item in store.sessions if item.id == session_id), None)
            if session is not None:
                session.message_count = len(messages)
    _save_store(store)


def _make_session_title(content: str) -> str:
    """用首条用户消息生成会话标题"""
    title = " ".join(content.strip().split())
    if not title:
        return "新对话"
    return title[:28] + ("..." if len(title) > 28 else "")
