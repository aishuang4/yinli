"""ScheduledJob 状态机与时钟推进。本模块不启动 ticker，也不注册 REST。"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
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
from src.scheduler.notifications import notify_firing_terminal, notify_job_paused, create_notification
from src.scheduler.settings import is_capability_gate_enabled
from src.scheduler.unattended_policy import (
    frozen_skills_require_bash,
    would_fail_unattended_tools,
)
from src.scheduler.schedule import (
    ScheduleError,
    compute_next_run_at,
    grace_seconds_for_schedule,
    load_timezone,
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
    get_scheduled_job_run_by_key,
    insert_scheduled_job,
    insert_scheduled_job_run,
    list_due_scheduled_jobs,
    list_non_terminal_scheduled_job_runs,
    get_workspace_resource,
    insert_scheduled_job,
    insert_scheduled_job_run,
    list_mcp_servers,
    list_project_resource_bindings,
    list_scheduled_job_runs,
    list_scheduled_job_runs_for_scheduled_for,
    list_scheduled_jobs,
    list_starting_scheduled_job_runs_without_chat_run,
    list_stuck_clock_scheduled_job_runs,
    list_skills,
    update_scheduled_job,
    update_scheduled_job_run,
    update_scheduled_job_run_and_job,
)
from src.storage.scheduled_job_records import UniqueConstraintError

logger = logging.getLogger(__name__)
ON_TIME_SLACK_SECONDS = 60
DUE_SLOT_TRIGGERS = frozenset({"schedule", "catch_up"})
_RECONFIRM_FIELDS = frozenset({
    "agent_id",
    "frozen_prompt",
    "skill_ids",
    "tool_scope",
    "capability_contract",
    "input_spec", "model_config_id",
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
        model_config_id: str = "",
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
        capability_contract: dict[str, Any] | None = None,
        task_intent: str = "",
        accept_partial: bool = False,
        required_intents: list[str] | None = None,
        job_id: str = "",
        persist_file: bool = True,
    ) -> ScheduledJob:
        """字段齐全后写入 pending_confirmation；未传 tool_scope 则全 false。"""

        self._require_project(workspace_id, project_id)
        if not str(name).strip():
            raise ScheduledJobValidationError("name 不能为空")
        if not str(agent_id).strip():
            raise ScheduledJobValidationError("agent_id 不能为空")
        self._require_agent_exists(agent_id)
        self._require_job_runner(agent_id)
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
        stored_contract: dict[str, Any] = capability_contract or {}
        if is_capability_gate_enabled():
            from src.scheduler.feasibility import contract_from_feasibility, evaluate_job_feasibility

            intent = str(task_intent or "").strip()
            if not intent:
                raise ScheduledJobValidationError("task_intent 不能为空")
            result = evaluate_job_feasibility(
                task_intent=intent,
                agent_id=agent_id,
                workspace_id=workspace_id,
                project_id=project_id,
                required_intents=tuple(required_intents or ()),
            )
            if result.verdict == "impossible":
                raise ScheduledJobValidationError(result.message)
            if result.verdict == "partial" and not accept_partial:
                raise ScheduledJobValidationError(result.message)
            stored_contract = contract_from_feasibility(result).to_storage_dict()
            scope = JobToolScope()
        record: dict[str, Any] | None = None
        try:
            record = insert_scheduled_job({
                **({"id": job_id} if str(job_id or "").strip() else {}),
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
                "model_config_id": model_config_id,
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
                "capability_contract": stored_contract,
            })
        except UniqueConstraintError as exc:
            raise ScheduledJobConflictError("同项目下任务名称已存在") from exc
        try:
            job = self._job(record, hydrate=False)
        except ScheduledJobValidationError:
            delete_scheduled_job(record["id"])
            raise
        if persist_file:
            self._persist_definition(job)
        return self._job(record)

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
        try:
            from src.catalog.sync_jobs import sync_job_definitions
            from src.storage.database import get_project

            project = get_project(project_id) if project_id else None
            raw_root = str((project or {}).get("rootPath") or "").strip()
            sync_job_definitions(
                project_root=Path(raw_root) if raw_root else None,
                project_id=project_id or "default",
            )
        except Exception:
            pass
        jobs: list[ScheduledJob] = []
        for item in list_scheduled_jobs(
            project_id=project_id, agent_id=agent_id, status=status,
        ):
            try:
                jobs.append(self._job(item))
            except ScheduledJobValidationError:
                continue
        return jobs

    def confirm(self, job_id: str, *, now: datetime | None = None, persist_file: bool = True) -> ScheduledJob:
        """对话 ConfirmJob 与 Jobs 页 REST 共用的启用路径；已 active 则幂等返回。"""

        job = self.get_job(job_id)
        if job.status == "active":
            return job
        if job.status != "pending_confirmation":
            raise ScheduledJobStateError("只有 pending_confirmation 任务可以确认启用")
        self._assert_transition(job.status, "active")
        if job.delivery_policy == "auto_send":
            raise ScheduledJobValidationError("Phase 1 不能启用 auto_send")
        self._assert_resources_available(job)
        self._require_job_runner(job.agent_id)
        contract_changes = self.assert_contract_satisfiable(job)
        next_run = compute_next_run_at(
            schedule_kind=job.schedule_kind,
            schedule_expr=job.schedule_expr,
            timezone_name=job.timezone,
            after=now or datetime.now(timezone.utc),
        )
        changes: dict[str, Any] = {
            "status": "active",
            "pause_reason": "",
            "next_run_at": to_utc_iso(next_run),
            **contract_changes,
        }
        return self._save_job(job_id, changes, persist_file=persist_file)

    def pause(self, job_id: str, *, reason: str = "user", persist_file: bool = True) -> ScheduledJob:
        job = self.get_job(job_id)
        if job.status == "paused":
            return job
        self._assert_transition(job.status, "paused")
        updated = self._save_job(job_id, {
            "status": "paused",
            "pause_reason": reason,
        }, persist_file=persist_file)
        if reason and reason != "user":
            notify_job_paused(updated)
        return updated

    def resume(self, job_id: str, *, now: datetime | None = None, persist_file: bool = True) -> ScheduledJob:
        job = self.get_job(job_id)
        if job.status == "active":
            return job
        if job.status != "paused":
            raise ScheduledJobStateError("只有 paused 任务可以恢复")
        self._assert_transition(job.status, "active")
        if job.delivery_policy == "auto_send":
            raise ScheduledJobValidationError("Phase 1 不能启用 auto_send")
        self._assert_resources_available(job)
        self._require_job_runner(job.agent_id)
        contract_changes = self.assert_contract_satisfiable(job)
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
            **contract_changes,
        }, persist_file=persist_file)

    def archive(self, job_id: str, *, now: datetime | None = None, persist_file: bool = True) -> ScheduledJob:
        job = self.get_job(job_id)
        if job.status == "archived":
            return job
        self._assert_transition(job.status, "archived")
        stamped = now or datetime.now(timezone.utc)
        return self._save_job(job_id, {
            "status": "archived",
            "archived_at": to_utc_iso(stamped),
        }, persist_file=persist_file)

    def update_job(
        self,
        job_id: str,
        changes: dict[str, Any],
        *,
        now: datetime | None = None,
        persist_file: bool = True,
    ) -> ScheduledJob:
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
        if "agent_id" in payload:
            next_agent_id = str(payload.get("agent_id") or "").strip()
            if not next_agent_id:
                raise ScheduledJobValidationError("agent_id 不能为空")
            self._require_agent_exists(next_agent_id)
            self._require_job_runner(next_agent_id)
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
        return self._save_job(job_id, payload, persist_file=persist_file)

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
        if trigger in DUE_SLOT_TRIGGERS and self.occupying_due_slot(job.id, canonical) is not None:
            raise ScheduledJobConflictError("同一 Job 的 scheduled_for 槽位已被占用")
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

    def list_due_jobs(self, *, now: datetime | None = None) -> list[ScheduledJob]:
        """active 且 next_run_at <= now 的 Job。"""

        stamped = to_utc_iso(now or datetime.now(timezone.utc))
        jobs: list[ScheduledJob] = []
        for item in list_due_scheduled_jobs(stamped):
            try:
                jobs.append(self._job(item))
            except ScheduledJobValidationError:
                continue
        return jobs

    def get_firing_by_key(
        self,
        job_id: str,
        scheduled_for: str,
        trigger: str,
    ) -> ScheduledJobRun | None:
        record = get_scheduled_job_run_by_key(
            job_id, to_utc_iso(parse_stored_datetime(scheduled_for)), trigger,
        )
        return self._firing(record) if record else None

    def list_slot_firings(self, job_id: str, scheduled_for: str) -> list[ScheduledJobRun]:
        canonical = to_utc_iso(parse_stored_datetime(scheduled_for))
        return [
            self._firing(item)
            for item in list_scheduled_job_runs_for_scheduled_for(job_id, canonical)
        ]

    def occupying_due_slot(self, job_id: str, scheduled_for: str) -> ScheduledJobRun | None:
        """schedule/catch_up 占用同一到期点；terminal 优先。manual 不占周期槽。"""

        occupants = [
            item for item in self.list_slot_firings(job_id, scheduled_for)
            if item.trigger in DUE_SLOT_TRIGGERS
        ]
        for item in occupants:
            if item.status in TERMINAL_FIRING_STATUSES:
                return item
        return occupants[0] if occupants else None

    def claim_queued(
        self,
        *,
        job_id: str,
        scheduled_for: str,
        trigger: str,
    ) -> ScheduledJobRun | None:
        """INSERT queued；UNIQUE 冲突或已有非终态 firing 时返回 None。"""

        try:
            return self.create_firing(
                job_id=job_id,
                scheduled_for=scheduled_for,
                trigger=trigger,
                status="queued",
            )
        except ScheduledJobConflictError:
            return None

    def mark_starting(self, firing_id: str, *, now: datetime | None = None) -> ScheduledJobRun:
        """queued -> starting。信号量拿到之后才调用。"""

        firing = self.get_firing(firing_id)
        if firing.status != "queued":
            return firing
        stamped = to_utc_iso(now or datetime.now(timezone.utc))
        record = update_scheduled_job_run(firing_id, {
            "status": "starting",
            "started_at": stamped,
        })
        if record is None:
            raise ScheduledJobNotFoundError(f"找不到 firing: {firing_id}")
        return self._firing(record)

    def mark_running(
        self,
        firing_id: str,
        chat_run_id: str,
        *,
        chat_session_id: str = "",
    ) -> ScheduledJobRun:
        """starting -> running，写入 chat_run_id。"""

        firing = self.get_firing(firing_id)
        if firing.status not in {"queued", "starting"}:
            return firing
        record = update_scheduled_job_run(firing_id, {
            "status": "running",
            "chat_run_id": chat_run_id,
            "chat_session_id": chat_session_id or firing.chat_session_id,
        })
        if record is None:
            raise ScheduledJobNotFoundError(f"找不到 firing: {firing_id}")
        return self._firing(record)

    def list_starting_without_chat_run(self) -> list[ScheduledJobRun]:
        return [
            self._firing(item)
            for item in list_starting_scheduled_job_runs_without_chat_run()
        ]

    def list_non_terminal_firings(self) -> list[ScheduledJobRun]:
        return [self._firing(item) for item in list_non_terminal_scheduled_job_runs()]

    def count_firings_today(self, job: ScheduledJob, *, now: datetime | None = None) -> int:
        """按 Job 时区统计当天 firing 数（含 missed / catch_up / manual）。"""

        stamped = now or datetime.now(timezone.utc)
        try:
            tz = load_timezone(job.timezone)
        except ScheduleError:
            tz = timezone.utc
        local_day = stamped.astimezone(tz).date()
        count = 0
        for firing in self.list_firings(job.id):
            try:
                created = parse_stored_datetime(firing.created_at)
            except ValueError:
                continue
            if created.astimezone(tz).date() == local_day:
                count += 1
        return count

    def decide_due_action(
        self,
        job: ScheduledJob,
        *,
        now: datetime | None = None,
    ) -> tuple[str, str]:
        """返回 (action, trigger)。action: fire / missed / skip_silent。"""

        if not job.next_run_at:
            return "skip_silent", "schedule"
        stamped = now or datetime.now(timezone.utc)
        scheduled_for = parse_stored_datetime(job.next_run_at)
        age = (stamped - scheduled_for).total_seconds()
        grace = grace_seconds_for_schedule(job.schedule_kind, job.schedule_expr)
        if age <= ON_TIME_SLACK_SECONDS:
            return "fire", "schedule"
        if job.missed_policy == "catch_up_once" and age <= grace:
            return "fire", "catch_up"
        if job.missed_policy == "skip_silent":
            return "skip_silent", "schedule"
        return "missed", "schedule"

    def repair_clock_if_terminal_row_exists(
        self,
        job: ScheduledJob,
        scheduled_for: str,
        trigger: str = "",
        *,
        now: datetime | None = None,
    ) -> ScheduledJob | None:
        """到期槽已有终态 schedule/catch_up 且时钟未推进时补跑 finalize_job_clock。"""

        _ = trigger
        return self.repair_clock_if_slot_occupied(job, scheduled_for, now=now)

    def repair_clock_if_slot_occupied(
        self,
        job: ScheduledJob,
        scheduled_for: str,
        *,
        now: datetime | None = None,
    ) -> ScheduledJob | None:
        """任一 trigger 的终态行占用该 scheduled_for 且 next_run_at 未推时只补时钟。"""

        occupant = self.occupying_due_slot(job.id, scheduled_for)
        if occupant is None or occupant.status not in TERMINAL_FIRING_STATUSES:
            return None
        canonical = to_utc_iso(parse_stored_datetime(scheduled_for))
        if job.next_run_at != canonical:
            return None
        return self.finalize_job_clock(
            job.id, occupant.id, now=now or datetime.now(timezone.utc),
        )

    def reconcile_interrupted_job_runs(
        self,
        *,
        now: datetime | None = None,
        live_firing_ids: set[str] | None = None,
    ) -> int:
        """非终态 firing 失败收口；终态但时钟未推的到期槽只 finalize_job_clock。"""

        live = live_firing_ids or set()
        stamped = now or datetime.now(timezone.utc)
        count = 0
        for firing in self.list_non_terminal_firings():
            if firing.id in live:
                continue
            self.complete_firing(
                firing.id,
                "failed",
                error_code="process_restart",
                error_message="进程已重启",
                now=stamped,
            )
            count += 1
        seen_jobs: set[str] = set()
        for item in list_stuck_clock_scheduled_job_runs():
            firing = self._firing(item)
            if firing.job_id in seen_jobs:
                continue
            self.finalize_job_clock(firing.job_id, firing.id, now=stamped)
            seen_jobs.add(firing.job_id)
            count += 1
        return count

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
        already_terminal = firing.status in TERMINAL_FIRING_STATUSES
        pending = firing if already_terminal else firing.model_copy(update={"status": status})
        try:
            clock_changes = self._clock_updates(job, pending, now=stamped_now)
        except ScheduleError as exc:
            raise ScheduledJobValidationError(str(exc)) from exc
        if already_terminal:
            if not clock_changes:
                return job
            return self._save_job(job.id, clock_changes)
        firing_changes = {
            "status": status,
            "error_code": error_code,
            "error_message": error_message,
            "output_manifest": dict(output_manifest or firing.output_manifest),
            "completed_at": to_utc_iso(stamped_now),
        }
        job_changes = dict(clock_changes)
        if status == "succeeded":
            job_changes["consecutive_successes"] = job.consecutive_successes + 1
            job_changes["consecutive_failures"] = 0
            job_changes["last_run_at"] = firing.scheduled_for
        elif status in {"failed", "missed"}:
            job_changes["consecutive_successes"] = 0
            job_changes["consecutive_failures"] = job.consecutive_failures + 1
            job_changes["last_run_at"] = firing.scheduled_for
        new_failures = int(job_changes.get("consecutive_failures", job.consecutive_failures))
        new_status = str(job_changes.get("status") or job.status)
        if (
            job.schedule_kind == "weekly"
            and new_status == "active"
            and status in {"failed", "missed"}
            and new_failures >= job.max_consecutive_failures
        ):
            job_changes["status"] = "paused"
            job_changes["pause_reason"] = "consecutive_failures"
        try:
            _, job_record = update_scheduled_job_run_and_job(
                firing_id, firing_changes, job.id, job_changes,
            )
        except UniqueConstraintError as exc:
            raise ScheduledJobConflictError("同项目下任务名称已存在") from exc
        if job_record is None:
            raise ScheduledJobNotFoundError(f"找不到 Job: {job.id}")
        updated = self._job(job_record)
        firing_after = self.get_firing(firing_id)
        notify_firing_terminal(updated, firing_after)
        if updated.status == "paused" and job.status != "paused":
            notify_job_paused(updated, firing=firing_after)
        return updated

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
                # 通常只消费当前到期槽之后的第一个锚点；若周期内还有更早的
                # 锚点已经经过（例如周一/周五任务周六才补跑），再从当前时间续算。
                # 单一周几没有中间锚点，直接推进一个周期；多周几任务可能
                # 在补跑前已经错过另一个锚点，才需要从当前时间续算。
                if len(kwargs["weekdays"]) == 1:
                    next_run = from_scheduled
                elif from_scheduled > now:
                    next_run = from_scheduled
                else:
                    next_run = next_weekly_anchor_after(now, **kwargs)
                changes["next_run_at"] = to_utc_iso(next_run)
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
            paused_job = self._save_job(job.id, {
                "status": "paused",
                "pause_reason": reason,
            })
            notify_job_paused(paused_job)
            paused.append(paused_job)
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
            paused = self._pause_jobs_claiming(
                "capability_unbound",
                project_id=project_id,
                mcp_server_id=resource_id,
            )
            if paused:
                return paused
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
            paused = self._pause_jobs_claiming("capability_unbound", mcp_server_id=resource_id)
            if paused:
                return paused
            return self.pause_for_resource_change(
                "needs_job_update",
                require_tool_flags=("allow_mcp",),
            )
        if resource_type in {"http_tool", "knowledge"}:
            paused = self._pause_jobs_claiming(
                "capability_unbound",
                http_or_knowledge_id=resource_id,
            )
            if paused:
                return paused
            return self.pause_for_resource_change(
                "needs_job_update",
                require_tool_flags=("allow_http", "allow_knowledge"),
            )
        return []

    def pause_jobs_for_removed_agent_tools(
        self,
        agent_id: str,
        *,
        mcp_ids: set[str] | None = None,
        knowledge_ids: set[str] | None = None,
        permission_revoked: bool = False,
    ) -> list[ScheduledJob]:
        """Agent 解绑 MCP/HTTP 或权限降级时，按合同精确暂停。"""

        reason = "permission_revoked" if permission_revoked else "capability_unbound"
        paused: list[ScheduledJob] = []
        for job in self.list_jobs(agent_id=agent_id):
            if job.status not in _PAUSABLE_JOB_STATUSES:
                continue
            if permission_revoked:
                if self._contract_needs_unattended_outbound(job):
                    paused.append(self.pause(job.id, reason=reason))
                continue
            if mcp_ids and self._job_claims(job, kinds={"mcp"}, resource_ids=mcp_ids):
                paused.append(self.pause(job.id, reason=reason))
            elif knowledge_ids and self._job_claims(
                job, kinds={"http", "knowledge"}, resource_ids=knowledge_ids,
            ):
                paused.append(self.pause(job.id, reason=reason))
        return paused

    def _pause_jobs_claiming(
        self,
        reason: str,
        *,
        project_id: str | None = None,
        mcp_server_id: str | None = None,
        http_or_knowledge_id: str | None = None,
    ) -> list[ScheduledJob]:
        paused: list[ScheduledJob] = []
        for job in self.list_jobs(project_id=project_id):
            if job.status not in _PAUSABLE_JOB_STATUSES:
                continue
            hit = False
            if mcp_server_id and self._job_claims(job, kinds={"mcp"}, server_id=mcp_server_id):
                hit = True
            if http_or_knowledge_id and self._job_claims(
                job, kinds={"http", "knowledge"}, resource_ids={http_or_knowledge_id},
            ):
                hit = True
            if hit:
                paused.append(self.pause(job.id, reason=reason))
        return paused

    def _job_claims(
        self,
        job: ScheduledJob,
        *,
        kinds: set[str],
        resource_ids: set[str] | None = None,
        server_id: str | None = None,
    ) -> bool:
        contract = job.capability_contract
        if contract.is_generate_only():
            return False
        for item in contract.claimed:
            if item.kind not in kinds:
                continue
            if resource_ids and item.resource_id in resource_ids:
                return True
            if server_id and item.server_id == server_id:
                return True
        return False

    def _contract_needs_unattended_outbound(self, job: ScheduledJob) -> bool:
        contract = job.capability_contract
        if contract.is_generate_only():
            return bool(job.tool_scope.allow_bash)
        if contract.allow_bash or contract.allow_http_request or contract.allow_web_fetch:
            return True
        return any(item.kind in {"mcp", "http", "knowledge"} for item in contract.claimed)

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

    def _persist_definition(self, job: ScheduledJob) -> None:
        try:
            from src.catalog.sync_jobs import persist_job_definition

            persist_job_definition(job)
        except Exception:
            logger.warning("写入 Job 定义文件失败: %s", getattr(job, "id", ""), exc_info=True)

    def _save_job(self, job_id: str, changes: dict[str, Any], *, persist_file: bool = True) -> ScheduledJob:
        try:
            record = update_scheduled_job(job_id, changes)
        except UniqueConstraintError as exc:
            raise ScheduledJobConflictError("同项目下任务名称已存在") from exc
        if record is None:
            raise ScheduledJobNotFoundError(f"找不到 Job: {job_id}")
        snapshot = self._job(record, hydrate=False)
        if persist_file:
            self._persist_definition(snapshot)
        return self._job(record)

    def assert_contract_satisfiable(self, job: ScheduledJob) -> dict[str, Any]:
        """confirm/resume 第二道门。generate-only 复用今日 would_fail；非空合同再验 claimed。"""

        from src.server.routes.agents import get_registered_agent

        agent = get_registered_agent(job.agent_id)
        contract = job.capability_contract
        if contract.is_generate_only() or contract.verdict == "generate_only":
            tool_scope = job.tool_scope
            force_bash = frozen_skills_require_bash(job.skill_ids)
            if force_bash:
                tool_scope = JobToolScope(
                    allow_bash=True,
                    allow_http=False,
                    allow_web=False,
                    allow_mcp=False,
                    allow_knowledge=False,
                )
            failing = would_fail_unattended_tools(tool_scope=tool_scope, agent=agent)
            if failing:
                raise ScheduledJobValidationError("would_fail_tools: " + ", ".join(failing))
            if force_bash and not job.tool_scope.allow_bash:
                return {"tool_scope": tool_scope.to_storage_dict()}
            return {}

        from src.scheduler.feasibility import evaluate_job_feasibility
        from src.scheduler.firing_toolkit import (
            CapabilityUnboundError,
            assemble_scheduled_firing_toolkit_spec,
            derived_tool_scope_from_spec,
        )

        intent = contract.task_intent or ""
        if not intent:
            raise ScheduledJobValidationError("合同缺少 task_intent，不能启用")
        live = evaluate_job_feasibility(
            task_intent=intent,
            agent_id=job.agent_id,
            workspace_id=job.workspace_id,
            project_id=job.project_id,
            required_intents=tuple(contract.required_intents),
            claimed_must_include=frozenset(item.capability_id for item in contract.claimed),
            include_suggestions=False,
        )
        if live.verdict == "impossible":
            raise ScheduledJobValidationError(live.message)
        try:
            spec = assemble_scheduled_firing_toolkit_spec(job, agent=agent)
        except CapabilityUnboundError as exc:
            raise ScheduledJobValidationError(str(exc)) from exc
        derived = derived_tool_scope_from_spec(spec)
        failing = would_fail_unattended_tools(
            tool_scope=derived,
            agent=agent,
            extra_tool_names=list(spec.get("enable_tools") or []),
        )
        if failing:
            raise ScheduledJobValidationError("would_fail_tools: " + ", ".join(failing))
        payload = contract.to_storage_dict()
        payload["confirmedAt"] = datetime.now(timezone.utc).isoformat()
        payload["assembledToolScope"] = {
            "allowBash": derived.allow_bash,
            "allowHttp": derived.allow_http,
            "allowWeb": derived.allow_web,
            "allowMcp": derived.allow_mcp,
            "allowKnowledge": derived.allow_knowledge,
        }
        return {
            "tool_scope": derived.to_storage_dict(),
            "capability_contract": payload,
        }

    def _agent_is_available(self, agent_id: str) -> bool:
        """default、Workspace 资源或已注册 catalog 任一命中即视为还在。"""

        ident = str(agent_id or "").strip()
        if not ident:
            return False
        if ident == "default":
            return True
        if get_workspace_resource("agent", ident) is not None:
            return True
        from src.server.routes.agents import get_registered_agent

        return get_registered_agent(ident) is not None

    def _require_agent_exists(self, agent_id: str) -> None:
        """登记草稿也要求执行者仍在；不代替能力门禁。"""

        if self._agent_is_available(agent_id):
            return
        raise ScheduledJobValidationError("找不到要执行定时任务的 Agent")

    def _require_job_runner(self, agent_id: str) -> None:
        """能力门禁开启时，执行者必须是 Job Agent。"""

        if not is_capability_gate_enabled():
            return
        from src.scheduler.job_toolkit import JobAgentError, require_job_runner
        from src.server.routes.agents import get_registered_agent

        try:
            require_job_runner(get_registered_agent(agent_id), agent_id=agent_id)
        except JobAgentError as exc:
            raise ScheduledJobValidationError(str(exc)) from exc

    def _assert_resources_available(self, job: ScheduledJob) -> None:
        """confirm / resume 时复检 Project、Agent 与冻结 Skill/MCP/HTTP 是否仍可用。"""

        project = get_project(job.project_id)
        if project is None:
            raise ScheduledJobStateError("Project 不存在，不能启用任务")
        if str(project.get("status") or "") == "archived":
            raise ScheduledJobStateError("Project 已归档，不能启用任务")
        if str(project.get("workspaceId") or "") != job.workspace_id:
            raise ScheduledJobStateError("project_id 与 workspace_id 不一致")

        if not self._agent_is_available(job.agent_id):
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

    def _job(self, record: dict[str, Any], *, hydrate: bool = True) -> ScheduledJob:
        payload = dict(record)
        if hydrate:
            try:
                from src.catalog.job_files import load_job_definition_file, overlay_instance_with_file

                file = load_job_definition_file(
                    job_id=str(payload.get("id") or ""),
                    workspace_id=str(payload.get("workspace_id") or ""),
                    project_id=str(payload.get("project_id") or ""),
                )
                if file is not None:
                    payload = overlay_instance_with_file(payload, file)
            except Exception:
                pass
        try:
            return ScheduledJob.model_validate(payload)
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
