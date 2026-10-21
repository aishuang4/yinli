"""交互式单 Agent 登记定时任务的普通工具组。

立即经 ScheduledJobService 写 SQLite，不是 external HITL 工具。
仅由单 Agent 交互 ReAct 注入；Team executor 与 scheduled firing 不得复用。
"""

from __future__ import annotations

import json
from typing import Any

from agentscope.message import TextBlock, ToolResultState
from agentscope.permission import PermissionBehavior, PermissionContext, PermissionDecision
from agentscope.tool import ToolBase, ToolChunk

from src.scheduler.models import ScheduledJob, ScheduledJobError
from src.scheduler.service import ScheduledJobService
from src.scheduler.feasibility import contract_from_feasibility, evaluate_job_feasibility
from src.scheduler.settings import is_capability_gate_enabled, is_scheduled_jobs_enabled

# AskUser 投递选项顺序由测试锁定：第一项且默认选中。
ASKUSER_DELIVERY_OPTIONS: tuple[str, str] = ("生成后通知我", "不投递")
ASKUSER_DELIVERY_DEFAULT = ASKUSER_DELIVERY_OPTIONS[0]
# 启用确认同样锁定选项顺序；resume 本身不会把 Job 变成 active。
ASKUSER_ENABLE_OPTIONS: tuple[str, str] = ("现在启用", "稍后再说")
ASKUSER_ENABLE_DEFAULT = ASKUSER_ENABLE_OPTIONS[0]
SCHEDULED_JOBS_DISABLED_MESSAGE = "调度未启用"
SCHEDULED_JOB_TOOL_NAMES: tuple[str, ...] = (
    "ScheduleJob", "ListJobs", "PauseJob", "ConfirmJob",
    "CheckJobFeasibility", "RecheckJobCapabilities",
)
ASKUSER_PARTIAL_OPTIONS: tuple[str, str] = ("按降级创建（只生成）", "先去补能力")

_DELIVERY_ASKUSER_HINT = (
    "收集投递策略时必须调用 AskUser，options 顺序固定为："
    f"{ASKUSER_DELIVERY_OPTIONS[0]}（默认选中）、{ASKUSER_DELIVERY_OPTIONS[1]}。"
    "Phase 1 不要提供「直接发给客户」或 auto_send。"
)
_ENABLE_ASKUSER_HINT = (
    "ScheduleJob 成功后任务仍是 pending_confirmation，不会进入调度。"
    "用户已明确要求启用/开启时，立即调用 ConfirmJob(jobId)；"
    "否则必须调用 AskUser，options 顺序固定为："
    f"{ASKUSER_ENABLE_OPTIONS[0]}（默认选中）、{ASKUSER_ENABLE_OPTIONS[1]}。"
    "用户选择现在启用后调用 ConfirmJob(jobId)。"
    "不要让用户必须打开「定时任务」页才能启用。"
)


def _job_agent_catalog_line() -> str:
    """把当前租户可用的 Job Agent 写进登记约定，避免模型拿 Chat 助手去跑任务。"""

    try:
        from src.scheduler.job_toolkit import JOB_ROLE, job_toolkit_of
        from src.server.routes.agents import list_registered_agents

        runners = [
            agent for agent in list_registered_agents()
            if getattr(agent, "role", "") == JOB_ROLE and job_toolkit_of(agent).required
        ]
    except Exception:
        runners = []
    if not runners:
        return "No Job Agent is configured. Tell the user to create one in Agents: set 用途 to 定时任务 Agent and mark at least one required tool."
    listing = "; ".join(f"{agent.name} ({agent.id})" for agent in runners)
    return f"Available Job Agents: {listing}. Pass agent_id of one of these to CheckJobFeasibility and ScheduleJob."


def scheduled_job_tools_cache_key(tools: list[ToolBase] | None = None) -> str:
    """交互登记路径的 runtime_features_cache_key 片段。"""

    names = [tool.name for tool in tools] if tools else list(SCHEDULED_JOB_TOOL_NAMES)
    fingerprint = ",".join(sorted(names))
    return f"scheduled-job-tools:{fingerprint}"


def scheduled_job_prompt_addendum() -> str:
    """注入主 Agent 的登记约定：AskUser 收集字段后 ScheduleJob，对话内 ConfirmJob 启用。"""

    catalog = _job_agent_catalog_line()
    return "\n".join([
        "",
        "Scheduled job registration:",
        catalog,
        "Scheduled jobs must run on a Job Agent, never the current chat Agent.",
        "First pick a Job Agent, then call CheckJobFeasibility(task_intent, agent_id=that Job Agent) before collecting a long schedule. Do not guess capabilities.",
        "CheckJobFeasibility does not strip the Job Agent's required tools; it only claims optional tools. If verdict is impossible, the Job Agent's required tools are unavailable — do not call ScheduleJob.",
        "If verdict is partial, AskUser options must be exactly: 按降级创建（只生成） (default, first), then 先去补能力. Mention 不能 and 发邮件/外发 vs 生成. Only ScheduleJob with accept_partial=true after they accept the downgrade. Required tools stay injected.",
        "Use AskUser to collect missing schedule fields, then call ScheduleJob with a complete payload including task_intent. There is no draft status.",
        f"When asking for delivery policy, AskUser options must be exactly: {ASKUSER_DELIVERY_OPTIONS[0]} (default, first), then {ASKUSER_DELIVERY_OPTIONS[1]}.",
        "Do not offer 直接发给客户, auto_send, or sending to the customer.",
        "ScheduleJob writes pending_confirmation immediately; that is not yet scheduled.",
        "After ScheduleJob, if the user already asked to enable/start the job, call ConfirmJob(jobId) immediately.",
        f"Otherwise AskUser with options exactly: {ASKUSER_ENABLE_OPTIONS[0]} (default, first), then {ASKUSER_ENABLE_OPTIONS[1]}.",
        "When the user chooses 现在启用, call ConfirmJob(jobId). AskUser resume itself does not activate the job.",
        "Do not tell the user they must open the 定时任务 page to enable the job. The page is an alternative path.",
        "If the user asks to enable an already-created pending job, ListJobs then ConfirmJob(job_id).",
        "ConfirmJob is the chat activation path and calls the same confirm() as REST; it is idempotent.",
        "To pause an existing job, ListJobs then PauseJob(job_id). PauseJob requires job_id; do not pass a fuzzy name.",
        "Do not insert a new job as a way to turn an existing one off.",
        "Omit tool_scope unless the user explicitly needs extra tools; the default freezes Bash/HTTP/Web/MCP/knowledge all false.",
        f"If a job tool returns {SCHEDULED_JOBS_DISABLED_MESSAGE}, tell the user scheduled jobs are not enabled yet.",
    ])


def build_scheduled_job_tools(
    *,
    project_slug: str,
    cwd: str = "",
    agent_id: str,
    session_id: str,
    service: ScheduledJobService | None = None,
) -> list[ToolBase]:
    """构造交互式单 Agent 的 ScheduleJob 工具组。"""

    kwargs = {
        "project_slug": project_slug,
        "cwd": cwd,
        "agent_id": agent_id,
        "session_id": session_id,
        "service": service or ScheduledJobService(),
    }
    return [
        ScheduleJob(**kwargs),
        ListJobs(**kwargs),
        PauseJob(**kwargs),
        ConfirmJob(**kwargs),
        CheckJobFeasibility(**kwargs),
        RecheckJobCapabilities(**kwargs),
    ]


class _ScheduledJobToolBase(ToolBase):
    """共享权限、失败形状与运行归属。"""

    is_mcp: bool = False
    is_concurrency_safe: bool = False
    is_external_tool: bool = False
    is_state_injected: bool = False
    project_slug: str = ""
    cwd: str = ""
    agent_id: str = ""
    session_id: str = ""

    def __init__(
        self,
        *,
        project_slug: str,
        cwd: str = "",
        agent_id: str,
        session_id: str,
        service: ScheduledJobService | None = None,
    ) -> None:
        super().__init__()
        self.project_slug = project_slug
        self.cwd = cwd
        self.agent_id = agent_id
        self.session_id = session_id
        self._service = service or ScheduledJobService()

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message=f"{self.name} writes or reads ScheduledJob via the control-plane service.",
            decision_reason="Scheduled job registration tool",
        )

    def _success(self, payload: dict[str, Any]) -> ToolChunk:
        return ToolChunk(
            content=[TextBlock(text=json.dumps(payload, ensure_ascii=False, indent=2))],
            state=ToolResultState.SUCCESS,
            is_last=True,
        )

    def _failure(self, error: Exception | str) -> ToolChunk:
        return ToolChunk(
            content=[TextBlock(text=json.dumps({"error": str(error)}, ensure_ascii=False))],
            state=ToolResultState.ERROR,
            is_last=True,
        )

    def _disabled(self) -> ToolChunk:
        return self._failure(SCHEDULED_JOBS_DISABLED_MESSAGE)

    def _require_enabled(self) -> ToolChunk | None:
        if is_scheduled_jobs_enabled():
            return None
        return self._disabled()


class ScheduleJob(_ScheduledJobToolBase):
    """字段齐全后立即写入 pending_confirmation。"""

    name: str = "ScheduleJob"
    description: str = (
        "Register a complete scheduled job for the current Project. "
        "executor=agent wakes a Job Agent with frozen_prompt. "
        "executor=script stores script_source and runs that file directly at due time, without a model. "
        "Do not mix agent fields into a script job, or script fields into an agent job. "
        "Writes pending_confirmation immediately; that does not start the schedule. "
        f"{_ENABLE_ASKUSER_HINT} "
        "Do not sleep until due time. There is no draft. Duplicate names in the same Project are rejected. "
        "Omit tool_scope unless the user explicitly needs extra tools; default freezes Bash/HTTP/Web/MCP/knowledge to false. "
        f"{_DELIVERY_ASKUSER_HINT} "
        f"Default delivery_policy is notify ({ASKUSER_DELIVERY_DEFAULT})."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Unique job name within the current Project."},
            "executor": {
                "type": "string",
                "enum": ["agent", "script"],
                "description": "agent runs a Job Agent. script runs script_source directly. Default agent.",
            },
            "schedule_kind": {
                "type": "string",
                "enum": ["weekly", "daily", "once"],
                "description": "weekly, daily at a fixed clock, or once. cron is not supported.",
            },
            "schedule_expr": {
                "type": "object",
                "description": (
                    'weekly: {"weekdays":["fri"],"at":"18:00"}; '
                    'daily: {"at":"09:00"}; '
                    'once: {"at":"2026-08-22T18:00:00"} without tz offset.'
                ),
            },
            "timezone": {"type": "string", "description": "IANA timezone, e.g. Asia/Shanghai."},
            "frozen_prompt": {
                "type": "string",
                "description": "Required for executor=agent. Frozen user prompt used at each firing. Omit for script.",
            },
            "script_filename": {
                "type": "string",
                "description": "Required for executor=script. File name such as check_cert.py or run.sh.",
            },
            "script_source": {
                "type": "string",
                "description": "Required for executor=script. Full script text saved with the job. Do not point at scratch or .venv.",
            },
            "interpreter": {
                "type": "string",
                "enum": ["python3", "bash"],
                "description": "Script runtime. Default python3. python3 requires .py; bash requires .sh.",
            },
            "args": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Arguments passed to the script on every firing.",
            },
            "timeout_seconds": {
                "type": "integer",
                "description": "Script timeout in seconds, 1 to 3600. Default 300.",
            },
            "summary": {"type": "string", "description": "Short human-readable summary."},
            "agent_id": {"type": "string", "description": "Job Agent that will run the task. Do not use the current chat Agent."},
            "skill_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Frozen Skill ids. Do not silently drop any.",
            },
            "tool_scope": {
                "type": "object",
                "description": "Frozen tool scope. Omit to write all-false JSON; do not copy interactive allow_bash.",
            },
            "input_spec": {"type": "object", "description": "Input file convention, relative to Project root."},
            "output_spec": {"type": "object", "description": "Expected outputs under the firing cwd."},
            "missed_policy": {
                "type": "string",
                "enum": ["catch_up_once", "mark_missed", "skip_silent"],
                "description": "What to do if the app was closed at due time. Default catch_up_once.",
            },
            "failure_policy": {
                "type": "string",
                "enum": ["notify_keep_schedule", "pause_job"],
                "description": "Failure policy. retry_once is not supported.",
            },
            "delivery_policy": {
                "type": "string",
                "enum": ["notify", "none"],
                "description": f"Default notify ({ASKUSER_DELIVERY_DEFAULT}). Phase 1 has no auto send.",
            },
            "task_intent": {
                "type": "string",
                "description": "User's original request, not the frozen_prompt. Required when capability_gate is on.",
            },
            "accept_partial": {
                "type": "boolean",
                "description": "True only after the user accepted a partial downgrade via AskUser.",
            },
            "required_intents": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional intent tags already extracted. Server still extracts from task_intent.",
            },
        },
        "required": ["name", "schedule_kind", "schedule_expr", "timezone"],
    }
    is_read_only: bool = False

    async def __call__(
        self,
        name: str,
        schedule_kind: str,
        schedule_expr: dict[str, Any] | str,
        timezone: str,
        frozen_prompt: str = "",
        summary: str = "",
        agent_id: str = "",
        executor: str = "agent",
        script_filename: str = "",
        script_source: str = "",
        interpreter: str = "python3",
        args: list[str] | None = None,
        timeout_seconds: int = 300,
        skill_ids: list[str] | None = None,
        tool_scope: dict[str, Any] | str | None = None,
        input_spec: dict[str, Any] | str | None = None,
        output_spec: dict[str, Any] | str | None = None,
        missed_policy: str = "catch_up_once",
        failure_policy: str = "notify_keep_schedule",
        delivery_policy: str = "notify",
        task_intent: str = "",
        accept_partial: bool = False,
        required_intents: list[str] | None = None,
        **kwargs: Any,
    ) -> ToolChunk:
        disabled = self._require_enabled()
        if disabled is not None:
            return disabled
        try:
            chosen = str(executor or "agent").strip() or "agent"
            if chosen == "script" and (
                str(frozen_prompt or "").strip()
                or str(agent_id or "").strip()
                or list(skill_ids or [])
                or tool_scope
            ):
                return self._failure("脚本任务不能同时填写 agent_id、frozen_prompt、skill_ids 或 tool_scope")
            resolved_agent_id = "" if chosen == "script" else (agent_id or self.agent_id).strip()
            job = self._service.create_job(
                project_slug=self.project_slug,
                cwd=self.cwd,
                name=name,
                agent_id=resolved_agent_id,
                schedule_kind=schedule_kind,
                schedule_expr=_coerce_mapping(schedule_expr, field="schedule_expr"),
                timezone=timezone,
                frozen_prompt="" if chosen == "script" else frozen_prompt,
                summary=summary or "",
                skill_ids=[] if chosen == "script" else list(skill_ids or []),
                tool_scope=None if chosen == "script" else _coerce_optional_mapping(tool_scope, field="tool_scope"),
                mode=chosen,
                script_filename=script_filename,
                script_source=script_source,
                interpreter=interpreter,
                script_args=list(args or []),
                timeout_seconds=timeout_seconds,
                input_spec=_coerce_optional_mapping(input_spec, field="input_spec") or {},
                output_spec=_coerce_optional_mapping(output_spec, field="output_spec") or {},
                missed_policy=missed_policy or "catch_up_once",
                failure_policy=failure_policy or "notify_keep_schedule",
                delivery_policy=delivery_policy or "notify",
                created_from_session_id=self.session_id,
                task_intent=task_intent,
                accept_partial=bool(accept_partial),
                required_intents=list(required_intents or []),
            )
        except (ScheduledJobError, ValueError) as exc:
            return self._failure(exc)
        return self._success({
            **_job_payload(job),
            "message": (
                "任务已登记为 pending_confirmation，尚未进入调度。"
                f"用户已明确要求启用时，立即调用 ConfirmJob({job.id})。"
                "否则必须调用 AskUser，options 顺序固定为："
                f"{ASKUSER_ENABLE_OPTIONS[0]}（默认选中）、{ASKUSER_ENABLE_OPTIONS[1]}。"
                f"用户选择{ASKUSER_ENABLE_OPTIONS[0]}后调用 ConfirmJob({job.id})。"
                "不要让用户必须打开「定时任务」页才能启用。"
            ),
        })


class ListJobs(_ScheduledJobToolBase):
    """列出当前 Project 的 Job 摘要。flag 关闭时仍可读。"""

    name: str = "ListJobs"
    description: str = (
        "List scheduled jobs in the current Project: id, name, status, next_run_at, last_outcome. "
        "Use this before PauseJob so you can pass a concrete job_id."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "description": "Optional status filter, e.g. active or pending_confirmation.",
            },
        },
        "required": [],
    }
    is_read_only: bool = True
    is_concurrency_safe: bool = True

    async def __call__(self, status: str = "", **kwargs: Any) -> ToolChunk:
        try:
            jobs = self._service.list_jobs(
                project_slug=self.project_slug,
                status=status or None,
            )
            return self._success({
                "jobs": [
                    {
                        **_job_payload(job),
                        "lastOutcome": _last_outcome(self._service, job.id),
                    }
                    for job in jobs
                ],
            })
        except ScheduledJobError as exc:
            return self._failure(exc)


class PauseJob(_ScheduledJobToolBase):
    """按 job_id 暂停已有任务，禁止用名称模糊匹配。"""

    name: str = "PauseJob"
    description: str = (
        "Pause an existing scheduled job. job_id is required; do not accept a fuzzy name. "
        "ListJobs first if the user said something like 关掉周五那个周报. "
        "Do not create a new job as a way to turn one off."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "job_id": {"type": "string", "description": "Exact scheduled job id. Required; names are not accepted."},
        },
        "required": ["job_id"],
    }
    is_read_only: bool = False

    async def __call__(self, job_id: str = "", **kwargs: Any) -> ToolChunk:
        if str(kwargs.get("name") or "").strip():
            return self._failure("PauseJob 必须提供 job_id，不能用名称暂停")
        if not str(job_id or "").strip():
            return self._failure("PauseJob 必须提供 job_id")
        disabled = self._require_enabled()
        if disabled is not None:
            return disabled
        try:
            job = self._service.get_job(job_id)
            if job.project_slug != self.project_slug:
                return self._failure(f"找不到 Job: {job_id}")
            paused = self._service.pause(job_id, reason="user")
        except ScheduledJobError as exc:
            return self._failure(exc)
        return self._success(_job_payload(paused))


class ConfirmJob(_ScheduledJobToolBase):
    """对话内启用路径：幂等调用 ScheduledJobService.confirm()。"""

    name: str = "ConfirmJob"
    description: str = (
        "Enable a pending scheduled job via ScheduledJobService.confirm(). "
        "This is the chat activation path after ScheduleJob (or after AskUser enable confirmation). "
        "Already-active jobs are returned unchanged. "
        "The 定时任务 page confirm button is an alternative path, not the required path."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "job_id": {"type": "string", "description": "Exact scheduled job id."},
        },
        "required": ["job_id"],
    }
    is_read_only: bool = False

    async def __call__(self, job_id: str = "", **kwargs: Any) -> ToolChunk:
        if not str(job_id or "").strip():
            return self._failure("ConfirmJob 必须提供 job_id")
        disabled = self._require_enabled()
        if disabled is not None:
            return disabled
        try:
            job = self._service.get_job(job_id)
            if job.project_slug != self.project_slug:
                return self._failure(f"找不到 Job: {job_id}")
            confirmed = self._service.confirm(job_id)
        except ScheduledJobError as exc:
            return self._failure(exc)
        payload = _job_payload(confirmed)
        next_run = confirmed.next_run_at or ""
        payload["message"] = (
            f"任务已启用。下次运行时间为 {next_run}。"
            if next_run
            else "任务已启用。"
        )
        return self._success(payload)


class CheckJobFeasibility(_ScheduledJobToolBase):
    """创建前能力门禁；不写库。"""

    name: str = "CheckJobFeasibility"
    description: str = (
        "Check whether a Job Agent can unattended-fulfill a scheduled task. "
        "Pass agent_id of a Job Agent; required tools are always kept. "
        "Only optional tools are claimed from the task_intent. "
        "Call this before ScheduleJob. Does not write a job."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "task_intent": {
                "type": "string",
                "description": "User's original request, e.g. 每周五给客户发周报邮件.",
            },
            "agent_id": {
                "type": "string",
                "description": "Job Agent to evaluate. Do not pass a chat Agent.",
            },
            "required_intents": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": ["task_intent"],
    }
    is_read_only: bool = True
    is_concurrency_safe: bool = True

    async def __call__(
        self,
        task_intent: str,
        agent_id: str = "",
        required_intents: list[str] | None = None,
        **kwargs: Any,
    ) -> ToolChunk:
        try:
            result = evaluate_job_feasibility(
                task_intent=task_intent,
                agent_id=(agent_id or self.agent_id).strip(),
                project_slug=self.project_slug,
                required_intents=tuple(required_intents or ()),
            )
        except Exception as exc:
            return self._failure(exc)
        return self._success(result.to_public_json())


class RecheckJobCapabilities(_ScheduledJobToolBase):
    """按用户原话重评已有 Job 合同；省略 task_intent 时用合同原话。"""

    name: str = "RecheckJobCapabilities"
    description: str = (
        "Re-evaluate an existing job's capability contract. "
        "Omit task_intent to reuse the stored original request. "
        "To add email sending to an existing job, pass the user's new original words."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "job_id": {"type": "string"},
            "task_intent": {
                "type": "string",
                "description": "Optional. Defaults to the contract's stored task_intent. Empty Phase 1 jobs require this.",
            },
        },
        "required": ["job_id"],
    }
    is_read_only: bool = False

    async def __call__(self, job_id: str = "", task_intent: str = "", **kwargs: Any) -> ToolChunk:
        if not str(job_id or "").strip():
            return self._failure("RecheckJobCapabilities 必须提供 job_id")
        disabled = self._require_enabled()
        if disabled is not None:
            return disabled
        try:
            job = self._service.get_job(job_id)
            if job.project_slug != self.project_slug:
                return self._failure(f"找不到 Job: {job_id}")
            intent = str(task_intent or "").strip() or job.capability_contract.task_intent
            if not intent:
                return self._failure("空合同必须提供 task_intent，不能回退 frozen_prompt")
            result = evaluate_job_feasibility(
                task_intent=intent,
                agent_id=job.agent_id,
                project_slug=job.project_slug,
            )
            updated = self._service.update_job(job_id, {
                "capability_contract": contract_from_feasibility(result).to_storage_dict(),
            })
        except ScheduledJobError as exc:
            return self._failure(exc)
        except Exception as exc:
            return self._failure(exc)
        return self._success({
            **_job_payload(updated),
            "feasibility": result.to_public_json(),
        })


def _job_payload(job: ScheduledJob) -> dict[str, Any]:
    return {
        "jobId": job.id,
        "name": job.name,
        "status": job.status,
        "nextRunAt": job.next_run_at,
        "lastRunAt": job.last_run_at,
        "mode": job.mode,
        "scheduleKind": job.schedule_kind,
        "timezone": job.timezone,
        "deliveryPolicy": job.delivery_policy,
        "agentId": job.agent_id,
        "toolScope": job.tool_scope.model_dump(),
        "pauseReason": job.pause_reason,
    }


def _last_outcome(service: ScheduledJobService, job_id: str) -> str:
    firings = service.list_firings(job_id)
    if not firings:
        return ""
    return firings[0].status


def _coerce_optional_mapping(value: dict[str, Any] | str | None, *, field: str) -> dict[str, Any] | None:
    if value is None or value == "":
        return None
    return _coerce_mapping(value, field=field)


def _coerce_mapping(value: dict[str, Any] | str, *, field: str) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{field} 必须是 JSON 对象") from exc
        if isinstance(parsed, dict):
            return parsed
    raise ValueError(f"{field} 必须是对象")
