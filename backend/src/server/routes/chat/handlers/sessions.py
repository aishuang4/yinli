"""会话查询端点 — 列表、消息、删除。"""

from fastapi import HTTPException

from src.server.routes.chat.models import ChatSession, StoredChatMessage
from src.server.routes.chat.router import router
from src.server.routes.chat.store import _load_store, _save_store
from src.server.routes.chat.plan_mode.models import PLAN_STATUS_FINALIZED
from src.storage.database import list_run_events, list_session_plans


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

    session_plans = list_session_plans(session_id)
    normalized = [
        _normalize_legacy_clarification_answer(message)
        for message in messages
    ]
    existing_request_ids = {
        str(request.get("id") or "")
        for message in normalized
        for request in message.clarification_requests
        if isinstance(request, dict)
    }
    synthetic_messages: list[StoredChatMessage] = []
    existing_plan_run_ids = {
        str(event.get("runId") or "")
        for message in normalized
        for event in message.runtime_events
        if isinstance(event, dict)
    }
    for plan in session_plans:
        plan_id = str(plan.get("id") or "")
        run_id = str(plan.get("runId") or "")
        if (
            not plan_id
            or plan.get("status") != PLAN_STATUS_FINALIZED
            or (run_id and run_id in existing_plan_run_ids)
        ):
            continue
        synthetic_messages.append(_synthetic_finalized_plan_message(plan))
        if run_id:
            existing_plan_run_ids.add(run_id)

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


def _synthetic_finalized_plan_message(plan: dict) -> StoredChatMessage:
    """为已落库但缺少 assistant 消息的 finalized Plan 生成历史最终答复。"""

    plan_id = str(plan.get("id") or "")
    created_at = str(plan.get("updatedAt") or plan.get("createdAt") or "")
    markdown = str(plan.get("markdown") or "")
    return StoredChatMessage(
        id=f"plan-finalized-{plan_id}",
        role="assistant",
        content=markdown,
        agent_id=str(plan.get("agentId") or "") or None,
        agent_name=None,
        model_name=None,
        error=None,
        runtimeEvents=[{
            "id": f"runtime-plan-finalized-{plan_id}",
            "runId": str(plan.get("runId") or ""),
            "sessionId": str(plan.get("sessionId") or ""),
            "type": "PLAN_FINALIZED",
            "source": "runtime",
            "sourceEventType": None,
            "scope": "plan",
            "agentId": str(plan.get("agentId") or ""),
            "agentName": "",
            "teamActivityId": None,
            "status": "completed",
            "reason": "Plan finalized as final answer.",
            "timestamp": created_at,
            "durationMs": None,
            "payload": {
                "planId": plan_id,
                "planFile": f".agent-workstation/plans/{plan.get('sessionId')}/{plan_id}.md",
                "title": str(plan.get("title") or ""),
                "goal": str(plan.get("goal") or ""),
                "markdown": str(plan.get("markdown") or ""),
                "steps": list(plan.get("steps") or []),
            },
        }],
        created_at=created_at,
    )
