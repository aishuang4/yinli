"""ScheduledJob 状态机与时钟推进。本模块不启动 ticker，也不注册 REST。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

from src.scheduler.models import (
    ALLOWED_JOB_TRANSITIONS,
    DEFAULT_JOB_TOOL_SCOPE,
    DELIVERY_POLICIES,
    FAILURE_POLICIES,
    FIRING_STATUSES,
    JOB_STATUSES,
    MISSED_POLICIES,
    NON_TERMINAL_FIRING_STATUSES,
    SCHEDULE_KINDS,
    TERMINAL_FIRING_STATUSES,
    JobToolScope,
    ScheduledJob,
    ScheduledJobConflictError,
    ScheduledJobNotFoundError,
    ScheduledJobRun,
    ScheduledJobStateError,
    ScheduledJobValidationError,
)
from src.scheduler.schedule import (
    ScheduleError,
    compute_next_run_at,
    next_weekly_anchor_after,
    parse_stored_datetime,
    to_utc_iso,
    validate_schedule_expr,
)
from src.storage.database import (
    delete_scheduled_job,
    get_project,
    get_scheduled_job,
    get_scheduled_job_run,
    insert_scheduled_job,
    insert_scheduled_job_run,
    list_scheduled_job_runs,
    list_scheduled_jobs,
    update_scheduled_job,
    update_scheduled_job_run,
)
from src.storage.scheduled_job_records import UniqueConstraintError


class ScheduledJobService:
    """Job 控制面唯一状态迁移入口。"""

    def create_job(
        self,
        *,
        workspace_id: str,
        project_id: str,
        name: str,
        agent_id: str,
        schedule_kind: str,
        schedule_expr: dict[str, Any],
        timezone: str,
        frozen_prompt: str,
        summary: str = "",
        skill_ids: list[str] | None = None,
        tool_scope: dict[str, Any] | JobToolScope | None = None,
        input_spec: dict[str, Any] | None = None,
        output_spec: dict[str, Any] | None = None,
        missed_policy: str = "catch_up_once",
        failure_policy: str = "notify_keep_schedule",
        delivery_policy: str = "notify",
        delivery_channel_ref: str = "",
        recipient_ids: list[str] | None = None,
        created_from_session_id: str = "",
        max_consecutive_failures: int = 3,
        max_firings_per_day: int = 8,
        required_successes: int = 3,
    ) -> ScheduledJob:
        """字段齐全后写入 pending_confirmation；未传 tool_scope 则全 false。"""

        self._require_project(workspace_id, project_id)
        if not str(name).strip():
            raise ScheduledJobValidationError("name 不能为空")
        if not str(agent_id).strip():
            raise ScheduledJobValidationError("agent_id 不能为空")
        if not str(frozen_prompt).strip():
            raise ScheduledJobValidationError("frozen_prompt 不能为空")
        canonical_expr, timezone_name = self._validate_schedule(
            schedule_kind, schedule_expr, timezone,
        )
        self._validate_failure_policy(failure_policy)
        self._validate_missed_policy(missed_policy)
        if delivery_policy not in DELIVERY_POLICIES:
            raise ScheduledJobValidationError(f"非法 delivery_policy: {delivery_policy}")
        scope = self._frozen_tool_scope(tool_scope)
        record: dict[str, Any] | None = None
        try:
            record = insert_scheduled_job({
                "workspace_id": workspace_id,
                "project_id": project_id,
                "name": name.strip(),
                "summary": summary,
                "status": "pending_confirmation",
                "pause_reason": "",
                "agent_id": agent_id,
                "mode": "agent",
                "schedule_kind": schedule_kind,
                "schedule_expr": canonical_expr,
                "timezone": timezone_name,
                "frozen_prompt": frozen_prompt,
                "skill_ids": list(skill_ids or []),
                "tool_scope": scope.to_storage_dict(),
                "input_spec": dict(input_spec or {}),
                "output_spec": dict(output_spec or {}),
                "missed_policy": missed_policy,
                "failure_policy": failure_policy,
                "delivery_policy": delivery_policy,
                "delivery_channel_ref": delivery_channel_ref,
                "recipient_ids": list(recipient_ids or []),
                "created_from_session_id": created_from_session_id,
                "max_consecutive_failures": max_consecutive_failures,
                "max_firings_per_day": max_firings_per_day,
                "required_successes": required_successes,
            })
        except UniqueConstraintError as exc:
            raise ScheduledJobConflictError("同项目下任务名称已存在") from exc
        try:
            return self._job(record)
        except ScheduledJobValidationError:
            delete_scheduled_job(record["id"])
            raise

    def get_job(self, job_id: str) -> ScheduledJob:
        record = get_scheduled_job(job_id)
        if record is None:
            raise ScheduledJobNotFoundError(f"找不到 Job: {job_id}")
        return self._job(record)

    def list_jobs(
        self,
        *,
        project_id: str | None = None,
        agent_id: str | None = None,
        status: str | None = None,
    ) -> list[ScheduledJob]:
        jobs: list[ScheduledJob] = []
        for item in list_scheduled_jobs(
            project_id=project_id, agent_id=agent_id, status=status,
        ):
            try:
                jobs.append(self._job(item))
            except ScheduledJobValidationError:
                continue
        return jobs

    def confirm(self, job_id: str, *, now: datetime | None = None) -> ScheduledJob:
        """Jobs 页 / ConfirmJob 主路径；已 active 则幂等返回。"""

        job = self.get_job(job_id)
        if job.status == "active":
            return job
        self._assert_transition(job.status, "active")
        if job.delivery_policy == "auto_send":
            raise ScheduledJobValidationError("Phase 1 不能启用 auto_send")
        next_run = compute_next_run_at(
            schedule_kind=job.schedule_kind,
            schedule_expr=job.schedule_expr,
            timezone_name=job.timezone,
            after=now or datetime.now(timezone.utc),
        )
        return self._save_job(job_id, {
            "status": "active",
            "pause_reason": "",
            "next_run_at": to_utc_iso(next_run),
        })

    def pause(self, job_id: str, *, reason: str = "user") -> ScheduledJob:
        job = self.get_job(job_id)
        if job.status == "paused":
            return job
        self._assert_transition(job.status, "paused")
        return self._save_job(job_id, {
            "status": "paused",
            "pause_reason": reason,
        })

    def resume(self, job_id: str, *, now: datetime | None = None) -> ScheduledJob:
        job = self.get_job(job_id)
        if job.status == "active":
            return job
        self._assert_transition(job.status, "active")
        self._require_project(job.workspace_id, job.project_id)
        next_run = compute_next_run_at(
            schedule_kind=job.schedule_kind,
            schedule_expr=job.schedule_expr,
            timezone_name=job.timezone,
            after=now or datetime.now(timezone.utc),
        )
        return self._save_job(job_id, {
            "status": "active",
            "pause_reason": "",
            "archived_at": None,
            "next_run_at": to_utc_iso(next_run),
        })

    def archive(self, job_id: str, *, now: datetime | None = None) -> ScheduledJob:
        job = self.get_job(job_id)
        if job.status == "archived":
            return job
        self._assert_transition(job.status, "archived")
        stamped = now or datetime.now(timezone.utc)
        return self._save_job(job_id, {
            "status": "archived",
            "archived_at": to_utc_iso(stamped),
        })

    def create_firing(
        self,
        *,
        job_id: str,
        scheduled_for: str,
        trigger: str = "schedule",
        status: str = "queued",
    ) -> ScheduledJobRun:
        """插入 firing 行，默认 queued。同一 Job 同时只允许一个非终态 firing。"""

        job = self.get_job(job_id)
        if status not in FIRING_STATUSES:
            raise ScheduledJobValidationError(f"非法 firing 状态: {status}")
        if trigger not in {"schedule", "manual", "catch_up"}:
            raise ScheduledJobValidationError(f"非法 firing trigger: {trigger}")
        if status in NON_TERMINAL_FIRING_STATUSES and self.has_non_terminal_firing(job_id):
            raise ScheduledJobConflictError("该 Job 已有未结束的 firing")
        canonical = to_utc_iso(parse_stored_datetime(scheduled_for))
        try:
            record = insert_scheduled_job_run({
                "job_id": job.id,
                "workspace_id": job.workspace_id,
                "project_id": job.project_id,
                "trigger": trigger,
                "scheduled_for": canonical,
                "status": status,
            })
        except UniqueConstraintError as exc:
            raise ScheduledJobConflictError(
                "同一 Job 的 (scheduled_for, trigger) 已存在"
            ) from exc
        return self._firing(record)

    def get_firing(self, firing_id: str) -> ScheduledJobRun:
        record = get_scheduled_job_run(firing_id)
        if record is None:
            raise ScheduledJobNotFoundError(f"找不到 firing: {firing_id}")
        return self._firing(record)

    def list_firings(self, job_id: str, *, status: str | None = None) -> list[ScheduledJobRun]:
        return [self._firing(item) for item in list_scheduled_job_runs(job_id, status=status)]

    def has_non_terminal_firing(self, job_id: str) -> bool:
        return any(
            item.status in NON_TERMINAL_FIRING_STATUSES
            for item in self.list_firings(job_id)
        )

    def complete_firing(
        self,
        firing_id: str,
        status: str,
        *,
        error_code: str = "",
        error_message: str = "",
        output_manifest: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> ScheduledJob:
        """将 firing 标终态并推进 Job 时钟；计数只在首次进入终态时更新。"""

        if status not in TERMINAL_FIRING_STATUSES:
            raise ScheduledJobValidationError(f"非法终态: {status}")
        firing = self.get_firing(firing_id)
        job = self.get_job(firing.job_id)
        stamped_now = now or datetime.now(timezone.utc)
        pending = firing.model_copy(update={"status": status})
        try:
            self._clock_updates(job, pending, now=stamped_now)
        except ScheduleError as exc:
            raise ScheduledJobValidationError(str(exc)) from exc
        already_terminal = firing.status in TERMINAL_FIRING_STATUSES
        if not already_terminal:
            stamped = to_utc_iso(stamped_now)
            update_scheduled_job_run(firing_id, {
                "status": status,
                "error_code": error_code,
                "error_message": error_message,
                "output_manifest": dict(output_manifest or firing.output_manifest),
                "completed_at": stamped,
            })
            if status == "succeeded":
                update_scheduled_job(job.id, {
                    "consecutive_successes": job.consecutive_successes + 1,
                    "consecutive_failures": 0,
                    "last_run_at": firing.scheduled_for,
                })
            elif status in {"failed", "missed", "cancelled"}:
                update_scheduled_job(job.id, {
                    "consecutive_successes": 0,
                    "consecutive_failures": job.consecutive_failures + 1,
                    "last_run_at": firing.scheduled_for,
                })
        job = self.finalize_job_clock(firing.job_id, firing_id, now=stamped_now)
        if already_terminal:
            return job
        refreshed_firing = self.get_firing(firing_id)
        if (
            job.schedule_kind == "weekly"
            and job.status == "active"
            and refreshed_firing.status in {"failed", "missed", "cancelled"}
            and job.consecutive_failures >= job.max_consecutive_failures
        ):
            return self._save_job(job.id, {
                "status": "paused",
                "pause_reason": "consecutive_failures",
            })
        return self.get_job(job.id)

    def finalize_job_clock(
        self,
        job_id: str,
        firing_id: str,
        *,
        now: datetime | None = None,
    ) -> ScheduledJob:
        """任一 firing 终态后推进时钟，避免 UNIQUE (job_id, scheduled_for, trigger) 卡死。"""

        job = self.get_job(job_id)
        firing = self.get_firing(firing_id)
        try:
            changes = self._clock_updates(
                job, firing, now=now or datetime.now(timezone.utc),
            )
        except ScheduleError as exc:
            raise ScheduledJobValidationError(str(exc)) from exc
        if not changes:
            return job
        return self._save_job(job.id, changes)

    def _clock_updates(
        self,
        job: ScheduledJob,
        firing: ScheduledJobRun,
        *,
        now: datetime,
    ) -> dict[str, Any]:
        if job.schedule_kind == "weekly":
            expr = validate_schedule_expr("weekly", job.schedule_expr)
            kwargs = {
                "weekdays": expr["weekdays"],
                "at": expr["at"],
                "timezone_name": job.timezone,
            }
            changes: dict[str, Any] = {}
            if firing.trigger == "manual":
                pass
            elif firing.trigger == "catch_up":
                from_scheduled = next_weekly_anchor_after(
                    parse_stored_datetime(firing.scheduled_for), **kwargs,
                )
                from_now = next_weekly_anchor_after(now, **kwargs)
                nxt = from_scheduled if from_scheduled >= from_now else from_now
                changes["next_run_at"] = to_utc_iso(nxt)
            else:
                nxt = next_weekly_anchor_after(
                    parse_stored_datetime(firing.scheduled_for), **kwargs,
                )
                changes["next_run_at"] = to_utc_iso(nxt)
            if (
                firing.status not in {"succeeded", "skipped"}
                and job.failure_policy == "pause_job"
                and job.status == "active"
            ):
                changes["status"] = "paused"
                changes["pause_reason"] = "failure_policy"
            return changes
        changes = {"next_run_at": None}
        if job.status == "archived":
            return changes
        if firing.status == "succeeded":
            changes["status"] = "completed"
            changes["pause_reason"] = ""
        elif firing.status != "skipped":
            changes["status"] = "paused"
            changes["pause_reason"] = "once_failed"
        return changes

    def pause_for_resource_change(
        self,
        reason: str,
        *,
        project_id: str | None = None,
        agent_id: str | None = None,
        skill_id: str | None = None,
    ) -> list[ScheduledJob]:
        """资源失效时立即暂停匹配的 active Job。"""

        if not project_id and not agent_id and not skill_id:
            raise ScheduledJobValidationError("pause_for_resource_change 需要过滤条件")
        paused: list[ScheduledJob] = []
        candidates = self.list_jobs(
            project_id=project_id,
            agent_id=agent_id,
            status="active",
        )
        for job in candidates:
            if skill_id and skill_id not in job.skill_ids:
                continue
            paused.append(self._save_job(job.id, {
                "status": "paused",
                "pause_reason": reason,
            }))
        return paused

    def _save_job(self, job_id: str, changes: dict[str, Any]) -> ScheduledJob:
        try:
            record = update_scheduled_job(job_id, changes)
        except UniqueConstraintError as exc:
            raise ScheduledJobConflictError("同项目下任务名称已存在") from exc
        if record is None:
            raise ScheduledJobNotFoundError(f"找不到 Job: {job_id}")
        return self._job(record)

    def _require_project(self, workspace_id: str, project_id: str) -> None:
        if not str(project_id).strip():
            raise ScheduledJobValidationError("project_id 不能为空")
        if not str(workspace_id).strip():
            raise ScheduledJobValidationError("workspace_id 不能为空")
        project = get_project(project_id)
        if project is None:
            raise ScheduledJobValidationError(f"找不到 Project: {project_id}")
        if str(project.get("workspaceId") or "") != workspace_id:
            raise ScheduledJobValidationError("project_id 与 workspace_id 不一致")

    def _validate_schedule(
        self,
        schedule_kind: str,
        schedule_expr: dict[str, Any],
        timezone_name: str,
    ) -> tuple[dict[str, Any], str]:
        if schedule_kind not in SCHEDULE_KINDS:
            raise ScheduledJobValidationError("Phase 1 仅支持 weekly 与 once")
        try:
            expr = validate_schedule_expr(schedule_kind, schedule_expr)
            tz = str(timezone_name).strip()
            compute_next_run_at(
                schedule_kind=schedule_kind,
                schedule_expr=expr,
                timezone_name=tz,
                after=datetime.now(timezone.utc),
            )
            return expr, tz
        except ScheduleError as exc:
            raise ScheduledJobValidationError(str(exc)) from exc

    def _validate_failure_policy(self, failure_policy: str) -> None:
        if failure_policy == "retry_once":
            raise ScheduledJobValidationError("failure_policy 已删除 retry_once")
        if failure_policy not in FAILURE_POLICIES:
            raise ScheduledJobValidationError(f"非法 failure_policy: {failure_policy}")

    def _validate_missed_policy(self, missed_policy: str) -> None:
        if missed_policy not in MISSED_POLICIES:
            raise ScheduledJobValidationError(f"非法 missed_policy: {missed_policy}")

    def _frozen_tool_scope(self, value: dict[str, Any] | JobToolScope | None) -> JobToolScope:
        if value is None:
            return JobToolScope()
        if isinstance(value, JobToolScope):
            return value
        return JobToolScope.model_validate(value)

    def _assert_transition(self, current: str, target: str) -> None:
        if current not in JOB_STATUSES or target not in JOB_STATUSES:
            raise ScheduledJobStateError(f"非法状态: {current} -> {target}")
        if target not in ALLOWED_JOB_TRANSITIONS.get(current, frozenset()):
            raise ScheduledJobStateError(f"不能从 {current} 迁移到 {target}")

    def _job(self, record: dict[str, Any]) -> ScheduledJob:
        try:
            return ScheduledJob.model_validate(record)
        except ValidationError as exc:
            raise ScheduledJobValidationError(str(exc)) from exc

    def _firing(self, record: dict[str, Any]) -> ScheduledJobRun:
        try:
            return ScheduledJobRun.model_validate(record)
        except ValidationError as exc:
            raise ScheduledJobValidationError(str(exc)) from exc


def default_job_tool_scope() -> dict[str, bool]:
    """给 REST / 工具层复用的全 false JSON 对象。"""

    return dict(DEFAULT_JOB_TOOL_SCOPE)
