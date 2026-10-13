"""定时任务管理 REST。flag 关闭时写接口 403；本模块不启动 ticker。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from src.catalog.job_files import job_definition_path, job_source_path_index
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
    agent_id: str = Field(default="", alias="agentId")
    model_config_id: str = Field(default="", alias="modelConfigId")
    mode: Literal["agent", "script"] = "agent"
    schedule_kind: Literal["weekly", "daily", "once"] = Field(alias="scheduleKind")
    schedule_expr: dict[str, Any] = Field(alias="scheduleExpr")
    timezone: str
    frozen_prompt: str = Field(default="", alias="frozenPrompt")
    script_filename: str = Field(default="", alias="scriptFilename")
    script_source: str = Field(default="", alias="scriptSource")
    interpreter: str = "python3"
    script_args: list[str] = Field(default_factory=list, alias="scriptArgs")
    timeout_seconds: int = Field(default=300, alias="timeoutSeconds")
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
    task_intent: str = Field(default="", alias="taskIntent")
    accept_partial: bool = Field(default=False, alias="acceptPartial")
    required_intents: list[str] = Field(default_factory=list, alias="requiredIntents")

    model_config = {"populate_by_name": True, "extra": "forbid"}


class ScheduledJobUpdateRequest(BaseModel):
    """部分更新；省略字段保持不变。"""

    name: str | None = None
    summary: str | None = None
    agent_id: str | None = Field(default=None, alias="agentId")
    model_config_id: str | None = Field(default=None, alias="modelConfigId")
    schedule_kind: Literal["weekly", "daily", "once"] | None = Field(default=None, alias="scheduleKind")
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


def _firing_preview(job: ScheduledJob) -> list[str]:
    if job.mode == "script":
        filename = str((job.input_spec or {}).get("script_filename") or "script")
        runtime = str((job.input_spec or {}).get("interpreter") or "python3")
        return [f"{runtime} {filename}"]
    try:
        from src.scheduler.firing_toolkit import assemble_scheduled_firing_toolkit_spec

        spec = assemble_scheduled_firing_toolkit_spec(job)
        return list(spec.get("firing_tools_preview") or [])
    except Exception:
        return ["Read", "Grep", "Glob", "Write", "Edit"]


@router.post("/scheduled-jobs/{job_id}/recheck-capabilities")
async def recheck_scheduled_job_capabilities(job_id: str) -> dict[str, Any]:
    """用合同原话重评能力；空合同 422。"""

    _require_enabled()
    from src.scheduler.feasibility import contract_from_feasibility, evaluate_job_feasibility

    try:
        job = _service.get_job(job_id)
        intent = job.capability_contract.task_intent
        if not intent:
            raise HTTPException(status_code=422, detail="空合同必须提供 task_intent")
        if _service.has_non_terminal_firing(job_id):
            raise HTTPException(status_code=409, detail="任务正在执行，不能重评合同")
        result = evaluate_job_feasibility(
            task_intent=intent,
            agent_id=job.agent_id,
            workspace_id=job.workspace_id,
            project_id=job.project_id,
            required_intents=tuple(job.capability_contract.required_intents),
        )
        updated = _service.update_job(job_id, {
            "capability_contract": contract_from_feasibility(result).to_storage_dict(),
        })
    except HTTPException:
        raise
    except Exception as exc:
        _raise_job_http(exc)
        raise
    return serialize_job(updated)


def _source_path_for(job: ScheduledJob, index: dict[str, str] | None = None) -> str:
    if index and job.id in index:
        return index[job.id]
    path = job_definition_path(
        workspace_id=job.workspace_id,
        project_id=job.project_id,
        job_id=job.id,
    )
    return str(path) if path.is_file() else ""


def serialize_job(job: ScheduledJob, *, source_path: str | None = None) -> dict[str, Any]:
    return {
        "id": job.id,
        "workspaceId": job.workspace_id,
        "projectId": job.project_id,
        "name": job.name,
        "summary": job.summary,
        "status": job.status,
        "pauseReason": job.pause_reason,
        "agentId": job.agent_id,
        "modelConfigId": job.model_config_id,
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
        "capabilityContract": job.capability_contract.model_dump(by_alias=True),
        "firingToolsPreview": _firing_preview(job),
        "createdAt": job.created_at,
        "updatedAt": job.updated_at,
        "archivedAt": job.archived_at,
        "sourcePath": source_path if source_path is not None else _source_path_for(job),
    }


def serialize_firing(firing: ScheduledJobRun) -> dict[str, Any]:
    manifest = dict(firing.output_manifest or {})
    result_text = str(manifest.get("resultText") or "").strip()
    if not result_text and firing.chat_session_id:
        from src.storage.database import get_latest_assistant_chat_message

        record = get_latest_assistant_chat_message(firing.chat_session_id)
        result_text = str((record or {}).get("content") or "").strip()
        if result_text:
            manifest["resultText"] = result_text
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
        "outputManifest": manifest,
        "resultText": result_text,
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
    from src.server.auth.ownership import filter_ids

    jobs = _service.list_jobs(project_id=project_id, status=status)
    allowed = set(filter_ids("scheduled_jobs", [str(item.id) for item in jobs]))
    jobs = [item for item in jobs if str(item.id) in allowed]
    index: dict[str, str] = {}
    try:
        from src.storage.database import get_project

        project = get_project(project_id) if project_id else None
        raw_root = str((project or {}).get("rootPath") or "").strip()
        workspace_id = str((project or {}).get("workspaceId") or "default")
        index = job_source_path_index(
            project_root=Path(raw_root) if raw_root else None,
            project_id=project_id or "default",
            workspace_id=workspace_id,
        )
    except Exception:
        index = {}
    return {
        "jobs": [serialize_job(item, source_path=_source_path_for(item, index)) for item in jobs],
        "enabled": is_scheduled_jobs_enabled(),
    }


class FeasibilityRequest(BaseModel):
    """创建前能力门禁查询。"""

    task_intent: str = Field(alias="taskIntent")
    agent_id: str = Field(alias="agentId")
    workspace_id: str = Field(alias="workspaceId")
    project_id: str = Field(alias="projectId")
    required_intents: list[str] = Field(default_factory=list, alias="requiredIntents")

    model_config = {"populate_by_name": True, "extra": "forbid"}


@router.post("/scheduled-jobs/feasibility")
async def check_scheduled_job_feasibility(data: FeasibilityRequest) -> dict[str, Any]:
    """不写库的能力门禁。"""

    from src.scheduler.feasibility import evaluate_job_feasibility

    result = evaluate_job_feasibility(
        task_intent=data.task_intent,
        agent_id=data.agent_id,
        workspace_id=data.workspace_id,
        project_id=data.project_id,
        required_intents=tuple(data.required_intents),
    )
    return result.to_public_json()


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
