"""ScheduledJob 状态机与时钟推进。本模块不启动 ticker，也不注册 REST。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
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
from src.scheduler.notifications import create_notification
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
    get_workspace_resource,
    insert_scheduled_job,
    insert_scheduled_job_run,
    list_mcp_servers,
    list_project_resource_bindings,
    list_scheduled_job_runs,
    list_scheduled_jobs,
    list_skills,
    update_scheduled_job,
    update_scheduled_job_run,
)
from src.storage.scheduled_job_records import UniqueConstraintError

_RECONFIRM_FIELDS = frozenset({
    "agent_id",
    "frozen_prompt",
    "skill_ids",
    "tool_scope",
    "input_spec",
    "output_spec",
    "delivery_policy",
    "delivery_channel_ref",
    "recipient_ids",
})
_SCHEDULE_FIELDS = frozenset({"schedule_kind", "schedule_expr", "timezone"})
_PAUSABLE_JOB_STATUSES = frozenset({"active", "pending_confirmation"})
_TOOL_SCOPE_FLAGS = frozenset({
    "allow_bash", "allow_http", "allow_web", "allow_mcp", "allow_knowledge",
})


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
        if job.status != "pending_confirmation":
            raise ScheduledJobStateError("只有 pending_confirmation 任务可以确认启用")
        self._assert_transition(job.status, "active")
        if job.delivery_policy == "auto_send":
            raise ScheduledJobValidationError("Phase 1 不能启用 auto_send")
        self._assert_resources_available(job)
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
        if job.status != "paused":
            raise ScheduledJobStateError("只有 paused 任务可以恢复")
        self._assert_transition(job.status, "active")
        if job.delivery_policy == "auto_send":
            raise ScheduledJobValidationError("Phase 1 不能启用 auto_send")
        self._assert_resources_available(job)
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

    def update_job(self, job_id: str, changes: dict[str, Any], *, now: datetime | None = None) -> ScheduledJob:
        """部分更新冻结规格。改投递/收件人/Skill 等需回到 pending_confirmation。"""

        job = self.get_job(job_id)
        if job.status in {"archived", "completed"}:
            raise ScheduledJobStateError(f"{job.status} 任务不能修改")
        payload = dict(changes)
        if "name" in payload:
            name = str(payload.get("name") or "").strip()
            if not name:
                raise ScheduledJobValidationError("name 不能为空")
            payload["name"] = name
        if "agent_id" in payload and not str(payload.get("agent_id") or "").strip():
            raise ScheduledJobValidationError("agent_id 不能为空")
        if "frozen_prompt" in payload and not str(payload.get("frozen_prompt") or "").strip():
            raise ScheduledJobValidationError("frozen_prompt 不能为空")
        if "delivery_policy" in payload and payload["delivery_policy"] not in DELIVERY_POLICIES:
            raise ScheduledJobValidationError(f"非法 delivery_policy: {payload['delivery_policy']}")
        if "failure_policy" in payload:
            self._validate_failure_policy(str(payload["failure_policy"]))
        if "missed_policy" in payload:
            self._validate_missed_policy(str(payload["missed_policy"]))
        if "tool_scope" in payload:
            payload["tool_scope"] = self._frozen_tool_scope(payload["tool_scope"]).to_storage_dict()
        if _SCHEDULE_FIELDS & payload.keys():
            canonical, timezone_name = self._validate_schedule(
                str(payload.get("schedule_kind") or job.schedule_kind),
                payload.get("schedule_expr") or job.schedule_expr,
                str(payload.get("timezone") or job.timezone),
            )
            if "schedule_kind" in payload:
                payload["schedule_kind"] = str(payload["schedule_kind"])
            if "schedule_expr" in payload:
                payload["schedule_expr"] = canonical
            if "timezone" in payload:
                payload["timezone"] = timezone_name
        needs_reconfirm = bool(_RECONFIRM_FIELDS & payload.keys()) and job.status in {"active", "paused"}
        if needs_reconfirm:
            payload["status"] = "pending_confirmation"
            payload["next_run_at"] = None
            payload["pause_reason"] = ""
        elif job.status == "active" and _SCHEDULE_FIELDS & payload.keys():
            next_run = compute_next_run_at(
                schedule_kind=str(payload.get("schedule_kind") or job.schedule_kind),
                schedule_expr=payload.get("schedule_expr") or job.schedule_expr,
                timezone_name=str(payload.get("timezone") or job.timezone),
                after=now or datetime.now(timezone.utc),
            )
            payload["next_run_at"] = to_utc_iso(next_run)
        if not payload:
            return job
        return self._save_job(job_id, payload)

    def run_now(self, job_id: str, *, now: datetime | None = None) -> ScheduledJobRun:
        """立即插入 trigger=manual 的 queued firing；已有非终态 firing 时冲突。"""

        job = self.get_job(job_id)
        if job.status != "active":
            raise ScheduledJobStateError("只有 active 任务可以立即执行")
        stamped = now or datetime.now(timezone.utc)
        last_error: ScheduledJobConflictError | None = None
        for offset in range(16):
            try:
                return self.create_firing(
                    job_id=job.id,
                    scheduled_for=to_utc_iso(
                        stamped + timedelta(microseconds=offset),
                        keep_microseconds=True,
                    ),
                    trigger="manual",
                    status="queued",
                )
            except ScheduledJobConflictError as exc:
                if self.has_non_terminal_firing(job.id):
                    raise
                last_error = exc
        assert last_error is not None
        raise last_error

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
        canonical = to_utc_iso(
            parse_stored_datetime(scheduled_for),
            keep_microseconds=(trigger == "manual"),
        )
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
        self.get_job(job_id)
        return [self._firing(item) for item in list_scheduled_job_runs(job_id, status=status)]

    def get_firing_for_job(self, job_id: str, firing_id: str) -> ScheduledJobRun:
        self.get_job(job_id)
        firing = self.get_firing(firing_id)
        if firing.job_id != job_id:
            raise ScheduledJobNotFoundError(f"找不到 firing: {firing_id}")
        return firing

    def cancel_firing(self, job_id: str, firing_id: str, *, now: datetime | None = None) -> ScheduledJobRun:
        firing = self.get_firing_for_job(job_id, firing_id)
        if firing.status in TERMINAL_FIRING_STATUSES:
            raise ScheduledJobConflictError("firing 已结束")
        self.complete_firing(firing_id, "cancelled", now=now)
        return self.get_firing(firing_id)

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
            elif status in {"failed", "missed"}:
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
            and refreshed_firing.status in {"failed", "missed"}
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
                firing.status in {"failed", "missed"}
                and job.failure_policy == "pause_job"
                and job.status == "active"
            ):
                changes["status"] = "paused"
                changes["pause_reason"] = "failure_policy"
            return changes
        if firing.trigger == "manual" and firing.status == "cancelled":
            return {}
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
        require_tool_flags: tuple[str, ...] = (),
    ) -> list[ScheduledJob]:
        """资源失效时立即暂停匹配的 active / pending Job，并写通知。"""

        flags = tuple(flag for flag in require_tool_flags if flag in _TOOL_SCOPE_FLAGS)
        if not project_id and not agent_id and not skill_id and not flags:
            raise ScheduledJobValidationError("pause_for_resource_change 需要过滤条件")
        paused: list[ScheduledJob] = []
        for job in self.list_jobs(project_id=project_id, agent_id=agent_id):
            if job.status not in _PAUSABLE_JOB_STATUSES:
                continue
            if skill_id and skill_id not in job.skill_ids:
                continue
            if flags and not any(getattr(job.tool_scope, flag, False) for flag in flags):
                continue
            saved = self._save_job(job.id, {
                "status": "paused",
                "pause_reason": reason,
            })
            self._notify_resource_pause(saved, reason)
            paused.append(saved)
        return paused

    def pause_jobs_for_binding_change(
        self,
        *,
        project_id: str,
        resource_type: str,
        resource_id: str,
    ) -> list[ScheduledJob]:
        """Project 资源停用或移出绑定时，暂停依赖该资源的 Job。"""

        if resource_type == "skill":
            return self.pause_for_resource_change(
                "skill_unbound",
                project_id=project_id,
                skill_id=resource_id,
            )
        if resource_type == "agent":
            return self.pause_for_resource_change(
                "agent_missing",
                project_id=project_id,
                agent_id=resource_id,
            )
        if resource_type == "mcp":
            return self.pause_for_resource_change(
                "needs_job_update",
                project_id=project_id,
                require_tool_flags=("allow_mcp",),
            )
        if resource_type == "http_tool":
            return self.pause_for_resource_change(
                "needs_job_update",
                project_id=project_id,
                require_tool_flags=("allow_http", "allow_knowledge"),
            )
        return []

    def pause_jobs_for_catalog_change(
        self,
        *,
        resource_type: str,
        resource_id: str,
    ) -> list[ScheduledJob]:
        """Skill / MCP / HTTP 目录卸载、停用或删除时暂停依赖该资源的 Job。"""

        if resource_type == "skill":
            return self.pause_for_resource_change("skill_unbound", skill_id=resource_id)
        if resource_type == "mcp":
            return self.pause_for_resource_change(
                "needs_job_update",
                require_tool_flags=("allow_mcp",),
            )
        if resource_type in {"http_tool", "knowledge"}:
            return self.pause_for_resource_change(
                "needs_job_update",
                require_tool_flags=("allow_http", "allow_knowledge"),
            )
        return []

    def _notify_resource_pause(self, job: ScheduledJob, reason: str) -> None:
        """资源变更暂停后写入一条应用内通知。"""

        create_notification(
            kind="job_paused",
            title=f"定时任务「{job.name}」已暂停",
            workspace_id=job.workspace_id,
            project_id=job.project_id,
            job_id=job.id,
            body=reason,
            payload={"pause_reason": reason},
        )

    def _save_job(self, job_id: str, changes: dict[str, Any]) -> ScheduledJob:
        try:
            record = update_scheduled_job(job_id, changes)
        except UniqueConstraintError as exc:
            raise ScheduledJobConflictError("同项目下任务名称已存在") from exc
        if record is None:
            raise ScheduledJobNotFoundError(f"找不到 Job: {job_id}")
        return self._job(record)

    def _assert_resources_available(self, job: ScheduledJob) -> None:
        """confirm / resume 时复检 Project、Agent 与冻结 Skill/MCP/HTTP 是否仍可用。"""

        project = get_project(job.project_id)
        if project is None:
            raise ScheduledJobStateError("Project 不存在，不能启用任务")
        if str(project.get("status") or "") == "archived":
            raise ScheduledJobStateError("Project 已归档，不能启用任务")
        if str(project.get("workspaceId") or "") != job.workspace_id:
            raise ScheduledJobStateError("project_id 与 workspace_id 不一致")

        if job.agent_id != "default":
            agent = get_workspace_resource("agent", job.agent_id)
            if agent is None:
                raise ScheduledJobStateError("Agent 不可用，不能启用任务")

        live = self._live_project_resources(job.project_id, job.workspace_id)
        skill_catalog = {str(item.get("id") or ""): item for item in list_skills()}
        for skill_id in job.skill_ids:
            skill = skill_catalog.get(skill_id)
            if (
                skill is None
                or str(skill.get("status") or "") != "installed"
                or not skill.get("enabled")
                or skill_id not in live["skill"]
            ):
                raise ScheduledJobStateError("冻结 Skill 已不可用，不能启用任务")

        if job.tool_scope.allow_mcp:
            mcp_catalog = {str(item.get("id") or ""): item for item in list_mcp_servers()}
            usable = [
                mcp_id
                for mcp_id in live["mcp"]
                if str(mcp_catalog.get(mcp_id, {}).get("status") or "") == "installed"
                and mcp_catalog.get(mcp_id, {}).get("enabled")
            ]
            if not usable:
                raise ScheduledJobStateError("MCP 绑定已失效，不能启用任务")
        if (
            job.tool_scope.allow_http or job.tool_scope.allow_knowledge
        ) and not live["http_tool"]:
            raise ScheduledJobStateError("HTTP 工具绑定已失效，不能启用任务")

    def _live_project_resources(
        self,
        project_id: str,
        workspace_id: str,
    ) -> dict[str, set[str]]:
        """enabled binding 且 catalog 资源仍存在、归属当前 Workspace。"""

        live: dict[str, set[str]] = {
            "agent": set(),
            "skill": set(),
            "mcp": set(),
            "http_tool": set(),
        }
        for binding in list_project_resource_bindings(project_id):
            resource_type = str(binding.get("resourceType") or "")
            resource_id = str(binding.get("resourceId") or "")
            if not binding.get("enabled") or resource_type not in live or not resource_id:
                continue
            resource = get_workspace_resource(resource_type, resource_id)
            if resource and resource.get("workspaceId") == workspace_id:
                live[resource_type].add(resource_id)
        return live

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
