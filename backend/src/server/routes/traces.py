"""Runtime trace routes derived from persisted chat runs."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.server.routes.chat import ChatTeamActivity, ChatToolRun, _load_store
from src.storage.database import list_run_events

router = APIRouter()


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


def _tool_call_count(messages: list[object]) -> int:
    total = 0
    for message in messages:
        total += len(getattr(message, "tool_calls", []) or [])
        for activity in getattr(message, "team_activities", []) or []:
            total += len(activity.tool_calls or [])
    return total


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
    return "completed" if messages else "empty"


def _event_trace_status(events: list[dict]) -> str:
    if any(event.get("status") == "failed" for event in events):
        return "failed"
    if any(event.get("status") == "waiting" for event in events):
        return "waiting"
    if events and not any(event.get("eventType") in {"ASSISTANT_MESSAGE", "ERROR"} for event in events):
        return "running"
    return "completed" if events else "empty"


def _summary_from_session(session: object, messages: list[object]) -> TraceSummary:
    events = list_run_events(session.id)
    tool_call_count = _event_tool_call_count(events) if events else _tool_call_count(messages)
    worker_count = _event_worker_count(events) if events else _worker_count(messages)
    status = _event_trace_status(events) if events else _trace_status(messages)
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


@router.get("", response_model=list[TraceSummary])
async def list_traces() -> list[TraceSummary]:
    """List runtime traces derived from persisted chat sessions."""
    store = _load_store()
    return [
        _summary_from_session(session, store.messages.get(session.id, []))
        for session in sorted(store.sessions, key=lambda item: item.updated_at, reverse=True)
    ]


@router.get("/{trace_id}", response_model=TraceDetail)
async def get_trace(trace_id: str) -> TraceDetail:
    """Return full runtime trace detail for a chat session."""
    store = _load_store()
    session = next((item for item in store.sessions if item.id == trace_id), None)
    if session is None:
        raise HTTPException(status_code=404, detail="Trace not found")
    messages = store.messages.get(trace_id, [])
    events = list_run_events(trace_id)
    summary = _summary_from_session(session, messages)
    return TraceDetail(
        **summary.model_dump(by_alias=True),
        messages=[
            TraceMessage(
                id=message.id,
                role=message.role,
                content=message.content,
                agentId=message.agent_id,
                agentName=message.agent_name,
                modelName=message.model_name,
                error=message.error,
                toolCalls=message.tool_calls,
                teamActivities=message.team_activities,
                createdAt=message.created_at,
            )
            for message in messages
        ],
        events=[TraceEvent.model_validate(event) for event in events],
    )
