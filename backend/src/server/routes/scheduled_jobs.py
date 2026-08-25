"""定时任务管理 REST。flag 关闭时写接口 403；本模块不启动 ticker。"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from src.scheduler.models import (
    JOB_STATUSES,
    ScheduledJob,
    ScheduledJobConflictError,
    ScheduledJobNotFoundError,
    ScheduledJobRun,
    ScheduledJobStateError,
    ScheduledJobValidationError,
)
from src.scheduler.service import ScheduledJobService
from src.scheduler.settings import is_scheduled_jobs_enabled

router = APIRouter()
_service = ScheduledJobService()

_DISABLED_DETAIL = "调度未启用"


class ScheduledJobCreateRequest(BaseModel):
    """创建定时任务；字段必须齐全。"""

    workspace_id: str = Field(alias="workspaceId")
    project_id: str = Field(alias="projectId")
    name: str
    agent_id: str = Field(alias="agentId")
    schedule_kind: Literal["weekly", "once"] = Field(alias="scheduleKind")
    schedule_expr: dict[str, Any] = Field(alias="scheduleExpr")
    timezone: str
    frozen_prompt: str = Field(alias="frozenPrompt")
    summary: str = ""
    skill_ids: list[str] = Field(default_factory=list, alias="skillIds")
    tool_scope: dict[str, Any] | None = Field(default=None, alias="toolScope")
    input_spec: dict[str, Any] | None = Field(default=None, alias="inputSpec")
    output_spec: dict[str, Any] | None = Field(default=None, alias="outputSpec")
    missed_policy: str = Field(default="catch_up_once", alias="missedPolicy")
    failure_policy: str = Field(default="notify_keep_schedule", alias="failurePolicy")
    delivery_policy: str = Field(default="notify", alias="deliveryPolicy")
    delivery_channel_ref: str = Field(default="", alias="deliveryChannelRef")
    recipient_ids: list[str] = Field(default_factory=list, alias="recipientIds")
    created_from_session_id: str = Field(default="", alias="createdFromSessionId")
    max_consecutive_failures: int = Field(default=3, alias="maxConsecutiveFailures")
    max_firings_per_day: int = Field(default=8, alias="maxFiringsPerDay")
    required_successes: int = Field(default=3, alias="requiredSuccesses")

    model_config = {"populate_by_name": True, "extra": "forbid"}


class ScheduledJobUpdateRequest(BaseModel):
    """部分更新；省略字段保持不变。"""

    name: str | None = None
    summary: str | None = None
    agent_id: str | None = Field(default=None, alias="agentId")
    schedule_kind: Literal["weekly", "once"] | None = Field(default=None, alias="scheduleKind")
    schedule_expr: dict[str, Any] | None = Field(default=None, alias="scheduleExpr")
    timezone: str | None = None
    frozen_prompt: str | None = Field(default=None, alias="frozenPrompt")
    skill_ids: list[str] | None = Field(default=None, alias="skillIds")
    tool_scope: dict[str, Any] | None = Field(default=None, alias="toolScope")
    input_spec: dict[str, Any] | None = Field(default=None, alias="inputSpec")
    output_spec: dict[str, Any] | None = Field(default=None, alias="outputSpec")
    missed_policy: str | None = Field(default=None, alias="missedPolicy")
    failure_policy: str | None = Field(default=None, alias="failurePolicy")
    delivery_policy: str | None = Field(default=None, alias="deliveryPolicy")
    delivery_channel_ref: str | None = Field(default=None, alias="deliveryChannelRef")
    recipient_ids: list[str] | None = Field(default=None, alias="recipientIds")
    max_consecutive_failures: int | None = Field(default=None, alias="maxConsecutiveFailures")
    max_firings_per_day: int | None = Field(default=None, alias="maxFiringsPerDay")
    required_successes: int | None = Field(default=None, alias="requiredSuccesses")

    model_config = {"populate_by_name": True, "extra": "forbid"}


def _require_enabled() -> None:
    if not is_scheduled_jobs_enabled():
        raise HTTPException(status_code=403, detail=_DISABLED_DETAIL)


def _raise_job_http(exc: Exception) -> None:
    if isinstance(exc, ScheduledJobValidationError):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if isinstance(exc, ScheduledJobNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, (ScheduledJobConflictError, ScheduledJobStateError)):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    raise exc


def serialize_job(job: ScheduledJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "workspaceId": job.workspace_id,
        "projectId": job.project_id,
        "name": job.name,
        "summary": job.summary,
        "status": job.status,
        "pauseReason": job.pause_reason,
        "agentId": job.agent_id,
        "mode": job.mode,
        "scheduleKind": job.schedule_kind,
        "scheduleExpr": job.schedule_expr,
        "timezone": job.timezone,
        "nextRunAt": job.next_run_at,
        "lastRunAt": job.last_run_at,
        "frozenPrompt": job.frozen_prompt,
        "skillIds": job.skill_ids,
        "toolScope": job.tool_scope.model_dump(),
        "inputSpec": job.input_spec,
        "outputSpec": job.output_spec,
        "missedPolicy": job.missed_policy,
        "failurePolicy": job.failure_policy,
        "deliveryPolicy": job.delivery_policy,
        "deliveryChannelRef": job.delivery_channel_ref,
        "recipientIds": job.recipient_ids,
        "unattendedPermission": job.unattended_permission,
        "autoSendEnabledAt": job.auto_send_enabled_at,
        "requiredSuccesses": job.required_successes,
        "consecutiveSuccesses": job.consecutive_successes,
        "consecutiveFailures": job.consecutive_failures,
        "maxConsecutiveFailures": job.max_consecutive_failures,
        "maxFiringsPerDay": job.max_firings_per_day,
        "createdFromSessionId": job.created_from_session_id,
        "createdAt": job.created_at,
        "updatedAt": job.updated_at,
        "archivedAt": job.archived_at,
    }


def serialize_firing(firing: ScheduledJobRun) -> dict[str, Any]:
    return {
        "id": firing.id,
        "jobId": firing.job_id,
        "workspaceId": firing.workspace_id,
        "projectId": firing.project_id,
        "trigger": firing.trigger,
        "scheduledFor": firing.scheduled_for,
        "status": firing.status,
        "chatSessionId": firing.chat_session_id,
        "chatRunId": firing.chat_run_id,
        "errorCode": firing.error_code,
        "errorMessage": firing.error_message,
        "outputManifest": firing.output_manifest,
        "deliveryStatus": firing.delivery_status,
        "startedAt": firing.started_at,
        "completedAt": firing.completed_at,
        "createdAt": firing.created_at,
        "updatedAt": firing.updated_at,
    }


@router.get("/scheduled-jobs")
async def list_scheduled_jobs(
    project_id: str | None = Query(default=None, alias="projectId"),
    status: str | None = Query(default=None),
) -> dict[str, Any]:
    """列出定时任务，并返回 scheduled_jobs.enabled，避免前端用写接口探测。"""

    if status and status not in JOB_STATUSES:
        raise HTTPException(status_code=422, detail=f"非法 Job 状态: {status}")
    jobs = _service.list_jobs(project_id=project_id, status=status)
    return {
        "jobs": [serialize_job(item) for item in jobs],
        "enabled": is_scheduled_jobs_enabled(),
    }


@router.post("/scheduled-jobs", status_code=201)
async def create_scheduled_job(data: ScheduledJobCreateRequest) -> dict[str, Any]:
    """字段齐全后写入 pending_confirmation。"""

    _require_enabled()
    payload = data.model_dump()
    try:
        job = _service.create_job(**payload)
    except Exception as exc:
        _raise_job_http(exc)
        raise
    return serialize_job(job)


@router.get("/scheduled-jobs/{job_id}")
async def get_scheduled_job(job_id: str) -> dict[str, Any]:
    """读取单个定时任务。"""

    try:
        job = _service.get_job(job_id)
    except Exception as exc:
        _raise_job_http(exc)
        raise
    return serialize_job(job)


@router.patch("/scheduled-jobs/{job_id}")
async def update_scheduled_job(job_id: str, data: ScheduledJobUpdateRequest) -> dict[str, Any]:
    """部分更新任务规格。"""

    _require_enabled()
    try:
        job = _service.update_job(job_id, data.model_dump(exclude_unset=True))
    except Exception as exc:
        _raise_job_http(exc)
        raise
    return serialize_job(job)


@router.post("/scheduled-jobs/{job_id}/pause")
async def pause_scheduled_job(job_id: str) -> dict[str, Any]:
    """暂停任务，不取消已在途的 firing。"""

    _require_enabled()
    try:
        job = _service.pause(job_id, reason="user")
    except Exception as exc:
        _raise_job_http(exc)
        raise
    return serialize_job(job)


@router.post("/scheduled-jobs/{job_id}/resume")
async def resume_scheduled_job(job_id: str) -> dict[str, Any]:
    """从 paused 恢复为 active。"""

    _require_enabled()
    try:
        job = _service.resume(job_id)
    except Exception as exc:
        _raise_job_http(exc)
        raise
    return serialize_job(job)


@router.post("/scheduled-jobs/{job_id}/confirm")
async def confirm_scheduled_job(job_id: str) -> dict[str, Any]:
    """Jobs 页启用主路径；Phase 1 拒绝 auto_send。"""

    _require_enabled()
    try:
        job = _service.confirm(job_id)
    except Exception as exc:
        _raise_job_http(exc)
        raise
    return serialize_job(job)


@router.post("/scheduled-jobs/{job_id}/run-now")
async def run_scheduled_job_now(job_id: str) -> dict[str, Any]:
    """插入 trigger=manual 的 queued firing；已有非终态 firing 时 409。"""

    _require_enabled()
    try:
        firing = _service.run_now(job_id)
    except Exception as exc:
        _raise_job_http(exc)
        raise
    return serialize_firing(firing)


@router.delete("/scheduled-jobs/{job_id}")
async def delete_scheduled_job(job_id: str) -> dict[str, Any]:
    """删除默认语义为归档，不级联抹 firing。"""

    _require_enabled()
    try:
        job = _service.archive(job_id)
    except Exception as exc:
        _raise_job_http(exc)
        raise
    return serialize_job(job)


@router.get("/scheduled-jobs/{job_id}/runs")
async def list_scheduled_job_runs(job_id: str) -> dict[str, Any]:
    """列出一个 Job 的 firing 历史。"""

    try:
        firings = _service.list_firings(job_id)
    except Exception as exc:
        _raise_job_http(exc)
        raise
    return {"runs": [serialize_firing(item) for item in firings]}


@router.get("/scheduled-jobs/{job_id}/runs/{firing_id}")
async def get_scheduled_job_run(job_id: str, firing_id: str) -> dict[str, Any]:
    """读取一次 firing。"""

    try:
        firing = _service.get_firing_for_job(job_id, firing_id)
    except Exception as exc:
        _raise_job_http(exc)
        raise
    return serialize_firing(firing)


@router.post("/scheduled-jobs/{job_id}/runs/{firing_id}/cancel")
async def cancel_scheduled_job_run(job_id: str, firing_id: str) -> dict[str, Any]:
    """取消非终态 firing。"""

    _require_enabled()
    try:
        firing = _service.cancel_firing(job_id, firing_id)
    except Exception as exc:
        _raise_job_http(exc)
        raise
    return serialize_firing(firing)
