"""会话持久化 — 读写 ChatStore，追加用户/助手消息并维护会话摘要。"""

import uuid
from datetime import datetime, timezone

from src.storage.database import (
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
    ChatModelCacheRun,
    ChatPlanRun,
    ChatSession,
    ChatSkillRun,
    ChatSubagentCall,
    ChatStore,
    ChatToolRun,
    ChatTeamActivity,
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
    team_id: str | None,
    team_name: str | None,
    model_name: str,
    content: str,
    images: list[ChatImagePayload],
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
        created_at=now,
    ))

    title = _make_session_title(content)
    existing = next((session for session in store.sessions if session.id == session_id), None)
    if existing:
        existing.agent_id = agent_id
        existing.agent_name = agent_name
        existing.team_id = team_id
        existing.team_name = team_name
        existing.updated_at = now
        existing.message_count = len(messages)
    else:
        store.sessions.append(ChatSession(
            id=session_id,
            title=title,
            agent_id=agent_id,
            agent_name=agent_name,
            team_id=team_id,
            team_name=team_name,
            created_at=now,
            updated_at=now,
            message_count=len(messages),
        ))

    _save_store(store)


def _append_assistant_message(
    *,
    session_id: str,
    agent_id: str,
    agent_name: str,
    model_name: str,
    content: str,
    error: str | None,
    thinking: str = "",
    skill_calls: list[ChatSkillRun] | None = None,
    tool_calls: list[ChatToolRun] | None = None,
    subagent_calls: list[ChatSubagentCall] | None = None,
    team_activities: list[ChatTeamActivity] | None = None,
    model_caches: list[ChatModelCacheRun] | None = None,
    plan_runs: list[ChatPlanRun] | None = None,
) -> None:
    """追加助手消息，并更新会话摘要"""
    now = datetime.now(timezone.utc).isoformat()
    store = _load_store()
    messages = store.messages.setdefault(session_id, [])
    incoming_tool_calls = tool_calls or []
    final_subagent_calls = subagent_calls if subagent_calls is not None else _extract_subagent_calls(incoming_tool_calls)
    final_tool_calls = [
        tool_call for tool_call in incoming_tool_calls
        if not _is_subagent_tool_name(tool_call.name)
    ]
    messages.append(StoredChatMessage(
        id=uuid.uuid4().hex,
        role="assistant",
        content=content,
        agent_id=agent_id,
        agent_name=agent_name,
        model_name=model_name,
        error=error,
        thinking=thinking,
        tool_calls=final_tool_calls,
        subagent_calls=final_subagent_calls,
        skill_calls=skill_calls or [],
        team_activities=team_activities or [],
        model_caches=model_caches or [],
        plan_runs=plan_runs or [],
        created_at=now,
    ))

    existing = next((session for session in store.sessions if session.id == session_id), None)
    if existing:
        existing.updated_at = now
        existing.message_count = len(messages)

    _save_store(store)


def _extract_subagent_calls(tool_calls: list[ChatToolRun]) -> list[ChatSubagentCall]:
    """从历史工具调用里派生子 Agent 调用记录，兼容旧运行链路。"""

    subagent_calls: list[ChatSubagentCall] = []
    for tool_call in tool_calls:
        if not _is_subagent_tool_name(tool_call.name):
            continue
        subagent_calls.append(ChatSubagentCall(
            id=tool_call.id,
            name=tool_call.name,
            args=tool_call.args,
            result=tool_call.result,
            status=tool_call.status,
        ))
    return subagent_calls


def _is_subagent_tool_name(name: str) -> bool:
    return name.startswith("CallAgent_") or name.startswith("子 Agent：")


def _make_session_title(content: str) -> str:
    """用首条用户消息生成会话标题"""
    title = " ".join(content.strip().split())
    if not title:
        return "新对话"
    return title[:28] + ("..." if len(title) > 28 else "")
