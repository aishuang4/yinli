"""定时任务领域模型。缺省 tool_scope 全 false，不得复用 RuntimeToolScope 全 True。"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

JobStatus = Literal["pending_confirmation", "active", "paused", "archived", "completed"]
ScheduleKind = Literal["weekly", "once"]
MissedPolicy = Literal["catch_up_once", "mark_missed", "skip_silent"]
FailurePolicy = Literal["notify_keep_schedule", "pause_job"]
DeliveryPolicy = Literal["none", "notify", "confirm_then_send", "auto_send"]
FiringTrigger = Literal["schedule", "manual", "catch_up"]
FiringStatus = Literal[
    "queued",
    "starting",
    "running",
    "succeeded",
    "failed",
    "cancelled",
    "missed",
    "skipped",
]
UnattendedPermission = Literal["overlay_workspace_write"]

JOB_STATUSES: frozenset[str] = frozenset(
    {"pending_confirmation", "active", "paused", "archived", "completed"}
)
FIRING_STATUSES: frozenset[str] = frozenset(
    {"queued", "starting", "running", "succeeded", "failed", "cancelled", "missed", "skipped"}
)
NON_TERMINAL_FIRING_STATUSES: frozenset[str] = frozenset({"queued", "starting", "running"})
TERMINAL_FIRING_STATUSES: frozenset[str] = frozenset(
    {"succeeded", "failed", "cancelled", "missed", "skipped"}
)
FAILURE_POLICIES: frozenset[str] = frozenset({"notify_keep_schedule", "pause_job"})
MISSED_POLICIES: frozenset[str] = frozenset({"catch_up_once", "mark_missed", "skip_silent"})
DELIVERY_POLICIES: frozenset[str] = frozenset({"none", "notify", "confirm_then_send", "auto_send"})
SCHEDULE_KINDS: frozenset[str] = frozenset({"weekly", "once"})

DEFAULT_JOB_TOOL_SCOPE: dict[str, bool] = {
    "allow_bash": False,
    "allow_http": False,
    "allow_web": False,
    "allow_mcp": False,
    "allow_knowledge": False,
}
DEFAULT_JOB_TOOL_SCOPE_JSON = json.dumps(DEFAULT_JOB_TOOL_SCOPE, separators=(",", ":"))

ALLOWED_JOB_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending_confirmation": frozenset({"active", "paused", "archived"}),
    "active": frozenset({"paused", "archived", "completed"}),
    "paused": frozenset({"active", "archived"}),
    "archived": frozenset(),
    "completed": frozenset({"archived"}),
}


class JobToolScope(BaseModel):
    """Job 冻结工具范围；缺键按 false，禁止落入 RuntimeToolScope 全 True。"""

    allow_bash: bool = False
    allow_http: bool = False
    allow_web: bool = False
    allow_mcp: bool = False
    allow_knowledge: bool = False

    model_config = {"extra": "ignore"}

    def to_storage_dict(self) -> dict[str, bool]:
        return self.model_dump()


class ScheduledJob(BaseModel):
    """SQLite scheduled_jobs 行的领域对象。"""

    id: str
    workspace_id: str
    project_id: str
    name: str
    summary: str = ""
    status: JobStatus
    pause_reason: str = ""
    agent_id: str
    mode: Literal["agent"] = "agent"
    schedule_kind: ScheduleKind
    schedule_expr: dict[str, Any]
    timezone: str
    next_run_at: str | None = None
    last_run_at: str | None = None
    frozen_prompt: str
    skill_ids: list[str] = Field(default_factory=list)
    tool_scope: JobToolScope = Field(default_factory=JobToolScope)
    input_spec: dict[str, Any] = Field(default_factory=dict)
    output_spec: dict[str, Any] = Field(default_factory=dict)
    missed_policy: MissedPolicy = "catch_up_once"
    failure_policy: FailurePolicy = "notify_keep_schedule"
    delivery_policy: DeliveryPolicy = "notify"
    delivery_channel_ref: str = ""
    recipient_ids: list[str] = Field(default_factory=list)
    unattended_permission: UnattendedPermission = "overlay_workspace_write"
    auto_send_enabled_at: str | None = None
    required_successes: int = 3
    consecutive_successes: int = 0
    consecutive_failures: int = 0
    max_consecutive_failures: int = 3
    max_firings_per_day: int = 8
    created_from_session_id: str = ""
    created_at: str
    updated_at: str
    archived_at: str | None = None

    @field_validator("status")
    @classmethod
    def _reject_draft(cls, value: str) -> str:
        if value == "draft":
            raise ValueError("Job 没有 draft 状态")
        if value not in JOB_STATUSES:
            raise ValueError(f"非法 Job 状态: {value}")
        return value

    @field_validator("failure_policy")
    @classmethod
    def _reject_retry_once(cls, value: str) -> str:
        if value == "retry_once":
            raise ValueError("failure_policy 已删除 retry_once")
        if value not in FAILURE_POLICIES:
            raise ValueError(f"非法 failure_policy: {value}")
        return value

    @field_validator("schedule_kind")
    @classmethod
    def _phase1_schedule_kind(cls, value: str) -> str:
        if value not in SCHEDULE_KINDS:
            raise ValueError("Phase 1 仅支持 weekly 与 once")
        return value

    @field_validator("mode")
    @classmethod
    def _mode_must_be_agent(cls, value: str) -> str:
        if value != "agent":
            raise ValueError("定时任务 mode 必须为 agent")
        return value

    @field_validator("tool_scope", mode="before")
    @classmethod
    def _tool_scope_missing_keys_false(cls, value: Any) -> Any:
        if value is None:
            return JobToolScope()
        if isinstance(value, JobToolScope):
            return value
        if isinstance(value, dict):
            return JobToolScope.model_validate(value)
        return value


class ScheduledJobRun(BaseModel):
    """一次 firing 记录；非终态含 queued。"""

    id: str
    job_id: str
    workspace_id: str
    project_id: str
    trigger: FiringTrigger
    scheduled_for: str
    status: FiringStatus
    chat_session_id: str = ""
    chat_run_id: str = ""
    error_code: str = ""
    error_message: str = ""
    output_manifest: dict[str, Any] = Field(default_factory=dict)
    delivery_status: str = "not_applicable"
    started_at: str | None = None
    completed_at: str | None = None
    created_at: str
    updated_at: str

    @field_validator("status")
    @classmethod
    def _known_firing_status(cls, value: str) -> str:
        if value not in FIRING_STATUSES:
            raise ValueError(f"非法 firing 状态: {value}")
        return value

    @field_validator("trigger")
    @classmethod
    def _known_trigger(cls, value: str) -> str:
        if value not in {"schedule", "manual", "catch_up"}:
            raise ValueError(f"非法 firing trigger: {value}")
        return value


class ScheduledJobError(Exception):
    """定时任务领域错误。"""


class ScheduledJobValidationError(ScheduledJobError):
    """字段不合法，对应 REST 422。"""


class ScheduledJobConflictError(ScheduledJobError):
    """唯一约束冲突。"""


class ScheduledJobStateError(ScheduledJobError):
    """非法状态迁移。"""


class ScheduledJobNotFoundError(ScheduledJobError):
    """Job 或 firing 不存在。"""
