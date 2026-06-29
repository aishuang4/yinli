"""Runtime trace routes derived from persisted chat runs."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from src.server.routes.chat import ChatTeamActivity, ChatToolRun, _load_store
from src.storage.database import count_run_events, list_run_events

router = APIRouter()

MAX_TRACE_SUMMARY_EVENTS = 80
MAX_TRACE_SUMMARIES = 100
TRACE_EVENT_PAGE_SIZE = 30
MAX_TRACE_EVENT_PAGE_SIZE = 100
MAX_TRACE_DETAIL_MESSAGES = 12
MAX_TRACE_MESSAGE_TEXT_LENGTH = 2500
MAX_TRACE_TOOL_TEXT_LENGTH = 1200
MAX_TRACE_ACTIVITY_TEXT_LENGTH = 1600
MAX_TRACE_PAYLOAD_TEXT_LENGTH = 1000


class TraceSummary(BaseModel):
    """A compact run trace summary."""

    id: str
    title: str
    agent_id: str | None = Field(default=None, alias="agentId")
    agent_name: str | None = Field(default=None, alias="agentName")
    team_id: str | None = Field(default=None, alias="teamId")
    team_name: str | None = Field(default=None, alias="teamName")
    message_count: int = Field(alias="messageCount")
    tool_call_count: int = Field(alias="toolCallCount")
    worker_count: int = Field(alias="workerCount")
    status: str
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")

    model_config = {"populate_by_name": True}


class TraceMessage(BaseModel):
    """One message inside a run trace."""

    id: str
    role: str
    content: str
    agent_id: str | None = Field(default=None, alias="agentId")
    agent_name: str | None = Field(default=None, alias="agentName")
    model_name: str | None = Field(default=None, alias="modelName")
    error: str | None = None
    tool_calls: list[ChatToolRun] = Field(default_factory=list, alias="toolCalls")
    team_activities: list[ChatTeamActivity] = Field(default_factory=list, alias="teamActivities")
    created_at: str = Field(alias="createdAt")

    model_config = {"populate_by_name": True}


class TraceEvent(BaseModel):
    """One normalized runtime event recorded during execution."""

    id: str
    session_id: str = Field(alias="sessionId")
    event_type: str = Field(alias="eventType")
    scope: str
    agent_id: str | None = Field(default=None, alias="agentId")
    agent_name: str | None = Field(default=None, alias="agentName")
    team_activity_id: str | None = Field(default=None, alias="teamActivityId")
    status: str
    title: str
    content: str
    payload: dict = Field(default_factory=dict)
    created_at: str = Field(alias="createdAt")
    sort_order: int = Field(alias="sortOrder")

    model_config = {"populate_by_name": True}


class TraceDetail(TraceSummary):
    """Full trace detail for one chat session."""

    messages: list[TraceMessage]
    events: list[TraceEvent] = Field(default_factory=list)
    event_total: int = Field(default=0, alias="eventTotal")


class TraceEventPage(BaseModel):
    """Paginated trace event response."""

    events: list[TraceEvent] = Field(default_factory=list)
    offset: int
    limit: int
    total: int
    has_more: bool = Field(alias="hasMore")

    model_config = {"populate_by_name": True}


def _tool_call_count(messages: list[object]) -> int:
    total = 0
    for message in messages:
        total += len(getattr(message, "tool_calls", []) or [])
        for activity in getattr(message, "team_activities", []) or []:
            total += len(activity.tool_calls or [])
    return total


def _trim_trace_text(value: str | None, limit: int = MAX_TRACE_MESSAGE_TEXT_LENGTH) -> str:
    """限制 Trace 详情中的大文本，避免前端一次性渲染过重。"""
    text = value or ""
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n\n...（已截断，完整内容请到 Chat 历史查看）"


def _trim_trace_payload(value: object, limit: int = MAX_TRACE_PAYLOAD_TEXT_LENGTH) -> object:
    """递归裁剪 payload 中的大字符串，保留可排查的结构。"""
    if isinstance(value, str):
        return _trim_trace_text(value, limit)
    if isinstance(value, list):
        items = [_trim_trace_payload(item, limit) for item in value[:30]]
        if len(value) > 30:
            items.append(f"...（还有 {len(value) - 30} 项已省略）")
        return items
    if isinstance(value, dict):
        items = {
            str(key): _trim_trace_payload(item, limit)
            for key, item in list(value.items())[:30]
        }
        if len(value) > 30:
            items["__truncated__"] = f"还有 {len(value) - 30} 个字段已省略"
        return items
    return value


def _trim_tool_run(tool_call: ChatToolRun) -> ChatToolRun:
    """裁剪工具调用中的 args/result，Trace 页只展示摘要。"""
    data = tool_call.model_dump(by_alias=True)
    data["args"] = _trim_trace_text(data.get("args"), MAX_TRACE_TOOL_TEXT_LENGTH)
    data["result"] = _trim_trace_text(data.get("result"), MAX_TRACE_TOOL_TEXT_LENGTH)
    return ChatToolRun.model_validate(data)


def _trim_activity(activity: ChatTeamActivity) -> ChatTeamActivity:
    """裁剪 Team 活动大字段，详情抽屉和 Chat 页保留完整信息。"""
    data = activity.model_dump(by_alias=True)
    for key in ("detail", "output", "report", "assignment", "expectedOutput"):
        data[key] = _trim_trace_text(data.get(key), MAX_TRACE_ACTIVITY_TEXT_LENGTH)
    data["toolCalls"] = [_trim_tool_run(item).model_dump(by_alias=True) for item in activity.tool_calls[:20]]
    return ChatTeamActivity.model_validate(data)


def _event_tool_call_count(events: list[dict]) -> int:
    return sum(1 for event in events if event.get("eventType") == "TOOL_CALL_START")


def _worker_count(messages: list[object]) -> int:
    worker_ids: set[str] = set()
    for message in messages:
        for activity in getattr(message, "team_activities", []) or []:
            if activity.role != "leader":
                worker_ids.add(activity.agent_id)
    return len(worker_ids)


def _event_worker_count(events: list[dict]) -> int:
    return len({
        str(event.get("agentId") or "")
        for event in events
        if event.get("scope") == "worker" and event.get("agentId")
    })


def _trace_status(messages: list[object]) -> str:
    if any(getattr(message, "error", None) for message in messages):
        return "failed"
    activities = [
        activity
        for message in messages
        for activity in (getattr(message, "team_activities", []) or [])
    ]
    if any(activity.status == "waiting" for activity in activities):
        return "waiting"
    if any(activity.status == "running" for activity in activities):
        return "running"
    if any(getattr(message, "role", "") == "assistant" for message in messages):
        return "completed"
    return "empty"


def _event_trace_status(events: list[dict]) -> str:
    if any(event.get("status") == "failed" for event in events):
        return "failed"

    if any(
        event.get("eventType") in {"ASSISTANT_MESSAGE", "REPLY_END"}
        and event.get("status") in {"", "completed"}
        for event in events
    ):
        return "completed"

    latest_waiting = next(
        (
            event
            for event in reversed(events)
            if event.get("status") == "waiting"
            or event.get("eventType") in {"HITL_REQUIRED", "CLARIFICATION_REQUIRED"}
        ),
        None,
    )
    latest_resolution = next(
        (
            event
            for event in reversed(events)
            if event.get("eventType") in {"HITL_RESOLVED", "ASSISTANT_MESSAGE", "REPLY_END", "ERROR"}
        ),
        None,
    )
    if latest_waiting and (
        not latest_resolution
        or int(latest_waiting.get("sortOrder") or 0) > int(latest_resolution.get("sortOrder") or 0)
    ):
        return "waiting"

    if events and not any(event.get("eventType") in {"ASSISTANT_MESSAGE", "ERROR", "REPLY_END"} for event in events):
        return "running"
    return "completed" if events else "empty"


def _summary_from_session(session: object, messages: list[object]) -> TraceSummary:
    events = list_run_events(session.id, include_payload=False, limit=MAX_TRACE_SUMMARY_EVENTS, latest=True)
    tool_call_count = _event_tool_call_count(events) if events else _tool_call_count(messages)
    worker_count = _event_worker_count(events) if events else _worker_count(messages)
    event_status = _event_trace_status(events) if events else "empty"
    message_status = _trace_status(messages)
    status = (
        message_status
        if message_status in {"completed", "failed"} and event_status in {"empty", "running", "waiting"}
        else event_status if events else message_status
    )
    return TraceSummary(
        id=session.id,
        title=session.title,
        agentId=session.agent_id,
        agentName=session.agent_name,
        teamId=session.team_id,
        teamName=session.team_name,
        messageCount=session.message_count,
        toolCallCount=tool_call_count,
        workerCount=worker_count,
        status=status,
        createdAt=session.created_at,
        updatedAt=session.updated_at,
    )


def _normalize_trace_events(events: list[dict]) -> list[TraceEvent]:
    """转换并裁剪 Trace 事件 payload。"""
    return [
        TraceEvent.model_validate({**event, "payload": _trim_trace_payload(event.get("payload", {}))})
        for event in events
    ]


@router.get("", response_model=list[TraceSummary])
async def list_traces() -> list[TraceSummary]:
    """List runtime traces derived from persisted chat sessions."""
    store = _load_store()
    return [
        _summary_from_session(session, store.messages.get(session.id, []))
        for session in sorted(store.sessions, key=lambda item: item.updated_at, reverse=True)[:MAX_TRACE_SUMMARIES]
    ]


@router.get("/{trace_id}", response_model=TraceDetail)
async def get_trace(trace_id: str) -> TraceDetail:
    """Return full runtime trace detail for a chat session."""
    store = _load_store()
    session = next((item for item in store.sessions if item.id == trace_id), None)
    if session is None:
        raise HTTPException(status_code=404, detail="Trace not found")
    messages = store.messages.get(trace_id, [])[-MAX_TRACE_DETAIL_MESSAGES:]
    total = count_run_events(trace_id)
    events = list_run_events(trace_id, limit=TRACE_EVENT_PAGE_SIZE, offset=0)
    summary = _summary_from_session(session, messages)
    return TraceDetail(
        **summary.model_dump(by_alias=True),
        messages=[
            TraceMessage(
                id=message.id,
                role=message.role,
                content=_trim_trace_text(message.content),
                agentId=message.agent_id,
                agentName=message.agent_name,
                modelName=message.model_name,
                error=message.error,
                toolCalls=[_trim_tool_run(item) for item in (message.tool_calls or [])[:20]],
                teamActivities=[_trim_activity(item) for item in (message.team_activities or [])],
                createdAt=message.created_at,
            )
            for message in messages
        ],
        events=_normalize_trace_events(events),
        eventTotal=total,
    )


@router.get("/{trace_id}/events", response_model=TraceEventPage)
async def get_trace_events(
    trace_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=TRACE_EVENT_PAGE_SIZE, ge=1, le=MAX_TRACE_EVENT_PAGE_SIZE),
) -> TraceEventPage:
    """Return one lightweight page of runtime trace events."""
    store = _load_store()
    session = next((item for item in store.sessions if item.id == trace_id), None)
    if session is None:
        raise HTTPException(status_code=404, detail="Trace not found")
    total = count_run_events(trace_id)
    events = list_run_events(trace_id, limit=limit, offset=offset)
    return TraceEventPage(
        events=_normalize_trace_events(events),
        offset=offset,
        limit=limit,
        total=total,
        hasMore=offset + len(events) < total,
    )
