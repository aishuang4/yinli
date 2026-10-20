"""把 Job 定义文件同步到 sqlite 运行实例。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from src.catalog.job_files import (
    DEFAULT_TOOL_SCOPE,
    JobFileRecord,
    discover_job_files,
    job_definition_path,
    write_job_file,
)

logger = logging.getLogger(__name__)


def _project_root_for(project_id: str) -> Path | None:
    del project_id
    return None


def _existing_job_file(job_id: str, *, project_id: str, workspace_id: str) -> JobFileRecord | None:
    for item in discover_job_files(
        project_root=_project_root_for(project_id),
        project_id=project_id,
        workspace_id=workspace_id,
    ):
        if item.id == job_id:
            return item
    return None


def persist_job_definition(job: Any) -> None:
    """把 ScheduledJob 实例的定义字段写回已有文件，否则写入 Workspace catalog。"""

    status = str(getattr(job, "status", "") or "")
    desired = "active" if status == "active" else "paused"
    existing = _existing_job_file(
        str(job.id),
        project_id=str(job.project_id),
        workspace_id=str(job.workspace_id),
    )
    contract = getattr(job, "capability_contract", None)
    contract_intent = str(getattr(contract, "task_intent", "") or "") if contract is not None else ""
    spec = dict(getattr(job, "input_spec", None) or {})
    record = JobFileRecord(
        id=str(job.id),
        name=str(job.name),
        workspace_id=str(job.workspace_id),
        project_id=str(job.project_id),
        agent_id=str(job.agent_id),
        frozen_prompt=str(job.frozen_prompt),
        mode=str(getattr(job, "mode", "agent") or "agent"),
        script_filename=str(spec.get("script_filename") or ""),
        script_source=str(spec.get("script_source") or ""),
        interpreter=str(spec.get("interpreter") or "python3"),
        script_args=[str(item) for item in list(spec.get("args") or [])],
        timeout_seconds=int(spec.get("timeout_seconds") or 300),
        schedule_kind=str(job.schedule_kind),
        schedule_expr=dict(job.schedule_expr or {}),
        timezone=str(job.timezone),
        desired_status=desired,
        summary=str(getattr(job, "summary", "") or ""),
        skill_ids=list(getattr(job, "skill_ids", []) or []),
        required_skills=[item.model_dump(by_alias=True) for item in list(getattr(job, "required_skills", []) or [])],
        context_policy=str(getattr(job, "context_policy", "fresh") or "fresh"),
        tool_scope=dict(getattr(job.tool_scope, "to_storage_dict", lambda: {})() or {}),
        execution_policy=job.execution_policy.model_dump(by_alias=True),
        missed_policy=str(job.missed_policy),
        failure_policy=str(job.failure_policy),
        delivery_policy=str(job.delivery_policy),
        task_intent=contract_intent or (existing.task_intent if existing else "") or str(job.name),
        source_scope=existing.source_scope if existing else "workspace",
        source_path=existing.source_path if existing else "",
    )
    path = job_definition_path(
        workspace_id=record.workspace_id,
        project_id=record.project_id,
        job_id=record.id,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    write_job_file(path, record)


def _definition_payload(record: JobFileRecord) -> dict[str, Any]:
    return {
        "name": record.name,
        "summary": record.summary,
        "agent_id": record.agent_id,
        "schedule_kind": record.schedule_kind,
        "schedule_expr": dict(record.schedule_expr),
        "timezone": record.timezone,
        "frozen_prompt": record.frozen_prompt,
        "skill_ids": list(record.skill_ids),
        # required_skills 是 ConfirmJob 生成的只读冻结快照，文件里的副本只供回显，不参与同步。
        "context_policy": record.context_policy or "fresh",
        "tool_scope": dict(record.tool_scope),
        **({"execution_policy": dict(record.execution_policy)} if record.execution_policy else {}),
        "missed_policy": record.missed_policy,
        "failure_policy": record.failure_policy,
        "delivery_policy": record.delivery_policy,
    }


def _canonical_expr(kind: str, expr: dict[str, Any]) -> dict[str, Any]:
    try:
        from src.scheduler.schedule import validate_schedule_expr

        return validate_schedule_expr(kind, expr)
    except Exception:
        return dict(expr or {})


def _scope_flags(scope: dict[str, Any]) -> dict[str, bool]:
    return {key: bool(scope.get(key, False)) for key in DEFAULT_TOOL_SCOPE}


def _same_definition(job: Any, payload: dict[str, Any]) -> bool:
    job_scope = dict(getattr(job.tool_scope, "to_storage_dict", lambda: {})() or {})
    return (
        str(job.name) == str(payload["name"])
        and str(job.summary or "") == str(payload.get("summary") or "")
        and str(job.agent_id) == str(payload["agent_id"])
        and str(job.schedule_kind) == str(payload["schedule_kind"])
        and _canonical_expr(str(job.schedule_kind), dict(job.schedule_expr or {}))
        == _canonical_expr(str(payload["schedule_kind"]), dict(payload.get("schedule_expr") or {}))
        and str(job.timezone) == str(payload["timezone"])
        and str(job.frozen_prompt) == str(payload["frozen_prompt"])
        and list(job.skill_ids or []) == list(payload.get("skill_ids") or [])
        and str(getattr(job, "context_policy", "fresh")) == str(payload.get("context_policy") or "fresh")
        and _scope_flags(job_scope) == _scope_flags(dict(payload.get("tool_scope") or {}))
        and (not payload.get("execution_policy") or job.execution_policy.model_dump(by_alias=True) == payload["execution_policy"])
        and str(job.missed_policy) == str(payload["missed_policy"])
        and str(job.failure_policy) == str(payload["failure_policy"])
        and str(job.delivery_policy) == str(payload["delivery_policy"])
    )


def sync_job_definitions(*, project_root=None, project_id: str = "default") -> dict[str, int]:
    """只同步 Session 內的 definition.md，不掃描 Workspace 或 Project。"""

    del project_root
    from src.scheduler.service import ScheduledJobService
    from src.storage.database import get_scheduled_job

    workspace_id = "default"
    service = ScheduledJobService()
    created = 0
    updated = 0
    activated = 0
    paused = 0
    for record in discover_job_files(project_id=project_id, workspace_id=workspace_id):
        existing = get_scheduled_job(record.id)
        if existing and str(existing.get("status") or "") in {"archived", "completed"}:
            continue
        try:
            if existing is None:
                job = service.create_job(
                    workspace_id=record.workspace_id or workspace_id,
                    project_id=record.project_id or project_id,
                    name=record.name,
                    agent_id="" if record.mode == "script" else record.agent_id,
                    schedule_kind=record.schedule_kind,
                    schedule_expr=dict(record.schedule_expr),
                    timezone=record.timezone,
                    frozen_prompt="" if record.mode == "script" else record.frozen_prompt,
                    summary=record.summary,
                    skill_ids=[] if record.mode == "script" else list(record.skill_ids),
                    required_skills=[] if record.mode == "script" else list(record.required_skills),
                    context_policy=record.context_policy,
                    tool_scope=None if record.mode == "script" else dict(record.tool_scope),
                    execution_policy=record.execution_policy or None,
                    mode=record.mode,
                    script_filename=record.script_filename,
                    script_source=record.script_source,
                    interpreter=record.interpreter,
                    script_args=list(record.script_args),
                    timeout_seconds=record.timeout_seconds,
                    missed_policy=record.missed_policy,
                    failure_policy=record.failure_policy,
                    delivery_policy=record.delivery_policy,
                    task_intent=record.task_intent or record.name,
                    job_id=record.id,
                    persist_file=False,
                )
                created += 1
            else:
                job = service.get_job(record.id)
                payload = _definition_payload(record)
                if not _same_definition(job, payload):
                    job = service.update_job(record.id, payload, persist_file=False)
                    updated += 1
            if record.desired_status == "active" and job.status == "pending_confirmation":
                service.confirm(job.id, persist_file=False)
                activated += 1
            elif record.desired_status == "active" and job.status == "paused" and job.pause_reason == "user":
                service.resume(job.id, persist_file=False)
                activated += 1
            elif record.desired_status == "paused" and job.status == "active":
                service.pause(job.id, reason="user", persist_file=False)
                paused += 1
        except Exception as exc:
            logger.warning("同步 Job 文件失败 %s: %s", record.source_path, exc)
            continue
    return {"created": created, "updated": updated, "activated": activated, "paused": paused}
