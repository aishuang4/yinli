"""Chat Trace：按一次 Run 列出 Observation，不再用 Session 或 run_events 当主数据。"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from src.storage.database import (
    get_chat_run,
    list_chat_runs,
    list_trace_observations,
)

router = APIRouter()

MAX_TRACE_SUMMARIES = 100
MAX_TITLE_LENGTH = 80


class TraceObservation(BaseModel):
    """一步闭环观测。"""

    id: str
    run_id: str = Field(alias="runId")
    session_id: str = Field(alias="sessionId")
    workspace_id: str = Field(default="default", alias="workspaceId")
    project_id: str = Field(default="default", alias="projectId")
    kind: str
    name: str
    parent_id: str | None = Field(default=None, alias="parentId")
    status: str
    started_at: str = Field(alias="startedAt")
    ended_at: str | None = Field(default=None, alias="endedAt")
    duration_ms: int | None = Field(default=None, alias="durationMs")
    input: dict = Field(default_factory=dict)
    output: dict = Field(default_factory=dict)
    metadata: dict = Field(default_factory=dict)

    model_config = {"populate_by_name": True}


class TraceSummary(BaseModel):
    """一条 Chat Run 的 Trace 摘要。"""

    id: str
    session_id: str = Field(alias="sessionId")
    workspace_id: str = Field(default="default", alias="workspaceId")
    project_id: str = Field(default="default", alias="projectId")
    title: str
    agent_id: str | None = Field(default=None, alias="agentId")
    agent_name: str | None = Field(default=None, alias="agentName")
    mode: str = "agent"
    status: str
    duration_ms: int | None = Field(default=None, alias="durationMs")
    observation_count: int = Field(default=0, alias="observationCount")
    tool_count: int = Field(default=0, alias="toolCount")
    created_at: str = Field(alias="createdAt")
    completed_at: str | None = Field(default=None, alias="completedAt")
    updated_at: str = Field(alias="updatedAt")

    model_config = {"populate_by_name": True}


class TraceDetail(TraceSummary):
    """单次 Run 的 Observation 时间线。"""

    observations: list[TraceObservation] = Field(default_factory=list)


def _parse_iso(value: str | None) -> datetime | None:
    text = str(value or "").replace("Z", "+00:00")
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _run_duration_ms(record: dict) -> int | None:
    start = _parse_iso(str(record.get("createdAt") or ""))
    if start is None:
        return None
    end = _parse_iso(str(record.get("completedAt") or "")) or datetime.now(timezone.utc)
    return max(0, int((end - start).total_seconds() * 1000))


def _title_from_task(task: str) -> str:
    text = " ".join(str(task or "").split())
    if not text:
        return "Chat Run"
    if len(text) <= MAX_TITLE_LENGTH:
        return text
    return f"{text[:MAX_TITLE_LENGTH].rstrip()}…"


def _summary_from_run(record: dict, observations: list[dict] | None = None) -> TraceSummary:
    rows = observations if observations is not None else []
    return TraceSummary(
        id=str(record.get("id") or ""),
        sessionId=str(record.get("sessionId") or ""),
        workspaceId=str(record.get("workspaceId") or "default"),
        projectId=str(record.get("projectId") or "default"),
        title=_title_from_task(str(record.get("task") or "")),
        agentId=record.get("agentId") or None,
        agentName=record.get("agentName") or None,
        mode=str(record.get("mode") or "agent"),
        status=str(record.get("status") or "running"),
        durationMs=_run_duration_ms(record),
        observationCount=len(rows),
        toolCount=sum(1 for item in rows if item.get("kind") == "tool"),
        createdAt=str(record.get("createdAt") or ""),
        completedAt=record.get("completedAt"),
        updatedAt=str(record.get("updatedAt") or record.get("createdAt") or ""),
    )


@router.get("", response_model=list[TraceSummary])
async def list_traces(
    session_id: str | None = Query(default=None, alias="sessionId"),
    scope: str = Query(default=""),
    project_id: str = Query(default="", alias="projectId"),
) -> list[TraceSummary]:
    """按 Session 列出 Trace；scope=tenant 才是當前 tenant 的全局視圖。"""

    _ = project_id
    if scope == "tenant":
        runs = list_chat_runs(MAX_TRACE_SUMMARIES)
    elif session_id:
        runs = list_chat_runs(MAX_TRACE_SUMMARIES, session_id=session_id)
    else:
        runs = list_chat_runs(MAX_TRACE_SUMMARIES)
    return [_summary_from_run(record) for record in runs]


@router.get("/{trace_id}", response_model=TraceDetail)
async def get_trace(
    trace_id: str,
    session_id: str | None = Query(default=None, alias="sessionId"),
    project_id: str = Query(default="", alias="projectId"),
) -> TraceDetail:
    """按 Run id 返回時間線，並在帶了 sessionId 時核對歸屬。"""

    _ = project_id
    record = get_chat_run(trace_id)
    if record is None or (session_id and str(record.get("sessionId") or "") != session_id):
        raise HTTPException(status_code=404, detail="Trace not found")
    observations = list_trace_observations(trace_id)
    if not observations:
        observations = [_synthetic_root(record)]
    summary = _summary_from_run(record, observations)
    return TraceDetail(
        **summary.model_dump(by_alias=True),
        observations=[TraceObservation.model_validate(item) for item in observations],
    )


def _synthetic_root(record: dict) -> dict:
    """旧 Run 没有 Observation 时仍给根节点，方便第二层看用户任务。"""

    return {
        "id": f"obs-run-{record.get('id')}",
        "runId": str(record.get("id") or ""),
        "sessionId": str(record.get("sessionId") or ""),
        "workspaceId": str(record.get("workspaceId") or "default"),
        "projectId": str(record.get("projectId") or "default"),
        "kind": "run",
        "name": str(record.get("agentName") or "Chat"),
        "parentId": None,
        "status": str(record.get("status") or "running"),
        "startedAt": str(record.get("createdAt") or ""),
        "endedAt": record.get("completedAt"),
        "durationMs": _run_duration_ms(record),
        "input": {"message": str(record.get("task") or "")},
        "output": {},
        "metadata": {"synthetic": True},
    }
