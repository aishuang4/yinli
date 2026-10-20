"""一次 queued firing 的执行：拿信号量后 starting，再走 start_scheduled_chat_run。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.agents.runtime_types import RuntimeToolScope
from src.scheduler.job_workspace import resolve_job_workspace
from src.scheduler.chat_bridge import (
    ScheduledChatLaunchError,
    ScheduledRunRequest,
    await_scheduled_chat_run,
    scan_scheduled_run_outputs,
    scheduled_chat_run_timeout_seconds,
    start_scheduled_chat_run,
)
from src.scheduler.models import (
    JobToolScope,
    ScheduledJob,
    ScheduledJobNotFoundError,
    ScheduledJobRun,
    TERMINAL_FIRING_STATUSES,
)
from src.scheduler.service import ScheduledJobService
from src.scheduler.skill_manifest import SINGLE_SKILL_MAX_CHARS, TOTAL_SKILL_MAX_CHARS
from src.runtime.scheduled_chat import (
    cancel_chat_background_run,
    get_chat_background_run,
    pop_scheduled_skill_audit,
)

logger = logging.getLogger(__name__)

# 写入 firing 记录的运行时 Skill Context 审计字段（均不含 Skill 正文）。
_RUNTIME_SKILL_AUDIT_KEYS = (
    "mode",
    "firstTurnSteps",
    "injectionOrder",
    "budget",
    "toolkit",
    "runSnapshot",
    "contextVerification",
    "reinjections",
    "rejectedViewerCalls",
    "validation",
    "errorCode",
    "errorMessage",
    "failedStep",
)


def runtime_tool_scope_from_job(scope: JobToolScope) -> RuntimeToolScope:
    """冻结 Job scope 转 RuntimeToolScope；缺键按 false，禁止全 True 默认。"""

    return RuntimeToolScope(
        allow_mcp=bool(scope.allow_mcp),
        allow_knowledge=bool(scope.allow_knowledge),
        allow_bash=bool(scope.allow_bash),
        allow_http=bool(scope.allow_http),
        allow_web=bool(scope.allow_web),
    )


def ensure_firing_terminal(
    service: ScheduledJobService,
    firing_id: str,
    *,
    status: str = "failed",
    error_code: str = "failed",
    error_message: str = "",
    output_manifest: dict[str, Any] | None = None,
) -> None:
    """任一路径收口终态并 finalize_job_clock；已终态则只补时钟。"""

    try:
        firing = service.get_firing(firing_id)
    except ScheduledJobNotFoundError:
        return
    if firing.status in TERMINAL_FIRING_STATUSES:
        service.finalize_job_clock(firing.job_id, firing.id)
        return
    service.complete_firing(
        firing_id,
        status,
        error_code=error_code,
        error_message=error_message,
        output_manifest=output_manifest,
    )


async def run_firing(
    job_id: str,
    firing_id: str,
    *,
    service: ScheduledJobService,
    semaphore: asyncio.Semaphore,
) -> None:
    """信号量在 Task 内、claim starting 之前获取。try/except/finally 必收口。"""

    terminal_status = "failed"
    error_code = "failed"
    error_message = ""
    output_manifest: dict[str, Any] | None = None
    chat_run_id = ""
    try:
        async with semaphore:
            firing = service.get_firing(firing_id)
            if firing.status != "queued":
                return
            job = service.get_job(job_id)
            output_manifest = {
                "skillContext": _skill_context_manifest(job, validation="pending"),
            }
            service.mark_starting(firing_id)
            if job.mode == "script":
                await _run_script_firing(service, job, firing_id)
                return
            req = _build_request(job, service.get_firing(firing_id))
            chat_run_id = await start_scheduled_chat_run(req)
            session_id = await _chat_session_id(chat_run_id)
            service.mark_running(
                firing_id, chat_run_id, chat_session_id=session_id,
            )
            timeout = scheduled_chat_run_timeout_seconds(job.agent_id)
            chat_status = await await_scheduled_chat_run(
                chat_run_id, timeout_seconds=timeout,
            )
            # Chat Run 终态后后台任务已 join，runtime 已按 firing 发布首轮 Skill Context 审计。
            runtime_audit = pop_scheduled_skill_audit(firing_id) or {}
            output_manifest = {
                **_scan_outputs(job, service.get_firing(firing_id)),
                "skillContext": _skill_context_manifest(
                    job,
                    validation="passed" if chat_status == "completed" else "runtime_failed",
                    owner_session_id=session_id,
                    firing_run_id=chat_run_id,
                    runtime=runtime_audit,
                ),
            }
            result_text = _assistant_result_text(session_id)
            if result_text:
                output_manifest = {**output_manifest, "resultText": result_text}
            if chat_status == "completed":
                terminal_status = "succeeded"
                error_code = ""
            elif chat_status == "cancelled":
                terminal_status = "failed"
                error_code = "timeout"
                error_message = "firing 等待 Chat Run 超时"
            else:
                terminal_status = "failed"
                permission_message = _permission_stop_message(session_id)
                runtime_error_code = str(runtime_audit.get("errorCode") or "")
                if permission_message:
                    error_code = "permission_required"
                    error_message = permission_message
                elif runtime_error_code.startswith("skill_"):
                    # 首轮 Skill 预加载 / Context 断言失败：模型未被调用，沿用 runtime 的稳定错误码。
                    error_code = runtime_error_code
                    error_message = str(runtime_audit.get("errorMessage") or "") or f"Chat Run 终态 {chat_status}"
                else:
                    error_code = "failed"
                    error_message = f"Chat Run 终态 {chat_status}"
            service.complete_firing(
                firing_id,
                terminal_status,
                error_code=error_code,
                error_message=error_message,
                output_manifest=output_manifest,
            )
    except asyncio.CancelledError:
        if chat_run_id:
            try:
                await cancel_chat_background_run(chat_run_id)
            except Exception:
                logger.exception("取消 Chat Run 失败 firing=%s", firing_id)
        terminal_status = "cancelled"
        error_code = "cancelled"
        error_message = "firing 已取消"
        raise
    except ScheduledChatLaunchError as exc:
        terminal_status = "failed"
        error_code = _launch_error_code(exc)
        error_message = str(exc)
        if output_manifest is None:
            output_manifest = {}
        skill_context = dict(output_manifest.get("skillContext") or {})
        if error_code.startswith("skill_"):
            skill_context.update({
                "validation": "failed",
                "errorCode": error_code,
                "errorMessage": error_message,
            })
            output_manifest["skillContext"] = skill_context
        logger.warning("scheduled chat launch failed firing=%s: %s", firing_id, exc)
    except Exception as exc:
        terminal_status = "failed"
        error_code = "failed"
        from src.exception_detail import format_agent_exception

        error_message = format_agent_exception(exc, prefix="定时任务执行失败")
        logger.error("run_firing failed firing=%s\n%s", firing_id, error_message)
    finally:
        # 异常 / 取消路径不会读取运行时审计，这里丢弃残留，避免进程内暂存无限增长。
        pop_scheduled_skill_audit(firing_id)
        ensure_firing_terminal(
            service,
            firing_id,
            status=terminal_status,
            error_code=error_code,
            error_message=error_message,
            output_manifest=output_manifest,
        )


async def _run_script_firing(
    service: ScheduledJobService,
    job: ScheduledJob,
    firing_id: str,
) -> None:
    """脚本任务不拉起模型，只在本次 firing 目录执行冻结脚本。"""

    from src.scheduler.script_job import ScriptJobError, execute_script_in_dir, script_spec_of

    firing = service.get_firing(firing_id)
    service.mark_running(firing_id, "")
    try:
        spec = script_spec_of(job.input_spec)
    except ScriptJobError as exc:
        service.complete_firing(
            firing_id, "failed", error_code="failed", error_message=str(exc),
        )
        return
    from src.scheduler.session_grant_check import owner_session_id

    owner = owner_session_id(job.id)
    if not owner:
        service.complete_firing(
            firing_id, "failed", error_code="failed", error_message="Job 沒有 owner Session",
        )
        return
    try:
        workspace = resolve_job_workspace(owner, job.id, firing.id)
        result = await execute_script_in_dir(
            source=str(spec["script_source"]),
            filename=str(spec["script_filename"]),
            interpreter=str(spec["interpreter"]),
            args=list(spec["args"]),
            timeout_seconds=int(spec["timeout_seconds"]),
            cwd=workspace,
        )
    except (OSError, ValueError, ScriptJobError) as exc:
        service.complete_firing(
            firing_id, "failed", error_code="failed", error_message=str(exc),
        )
        return
    output_manifest = _scan_outputs(job, service.get_firing(firing_id))
    output_manifest = {
        **output_manifest,
        "exitCode": result.exit_code,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "resultText": result.stdout.strip(),
    }
    if result.timed_out:
        service.complete_firing(
            firing_id,
            "failed",
            error_code="timeout",
            error_message="脚本执行超时",
            output_manifest=output_manifest,
        )
        return
    if result.exit_code == 0:
        service.complete_firing(
            firing_id, "succeeded", error_code="", output_manifest=output_manifest,
        )
        return
    service.complete_firing(
        firing_id,
        "failed",
        error_code="failed",
        error_message=result.stderr.strip() or f"脚本退出码 {result.exit_code}",
        output_manifest=output_manifest,
    )


def _build_request(job: ScheduledJob, firing: ScheduledJobRun) -> ScheduledRunRequest:
    from src.scheduler.firing_toolkit import (
        CapabilityUnboundError,
        assemble_scheduled_firing_toolkit_spec,
        derived_tool_scope_from_spec,
    )

    try:
        spec = assemble_scheduled_firing_toolkit_spec(job)
    except CapabilityUnboundError as exc:
        raise ScheduledChatLaunchError(str(exc)) from exc
    scope = derived_tool_scope_from_spec(spec)
    return ScheduledRunRequest(
        job_id=job.id,
        firing_id=firing.id,
        workspace_id=job.workspace_id,
        project_id=job.project_id,
        agent_id=job.agent_id,
        model_config_id=job.model_config_id,
        execution_policy=job.execution_policy.model_copy(deep=True),
        frozen_prompt=job.frozen_prompt,
        skill_ids=list(job.skill_ids),
        required_skills=[item.model_dump(by_alias=True) for item in list(job.required_skills or [])],
        context_policy=str(job.context_policy or "fresh"),
        tool_scope=runtime_tool_scope_from_job(scope),
        scheduled_for=firing.scheduled_for,
        trigger=firing.trigger,
    )


async def _chat_session_id(chat_run_id: str) -> str:
    run = await get_chat_background_run(chat_run_id)
    return run.session_id if run is not None else ""


def _scan_outputs(job: ScheduledJob, firing: ScheduledJobRun) -> dict[str, Any]:
    from src.scheduler.session_grant_check import owner_session_id

    owner = owner_session_id(job.id)
    if not owner:
        return {}
    try:
        workspace = resolve_job_workspace(owner, job.id, firing.id)
        return scan_scheduled_run_outputs(
            workspace,
            session_id=owner,
            run_id=firing.id,
        )
    except (OSError, ValueError):
        return {}


def _permission_stop_message(session_id: str) -> str:
    """人工需求只结束这次 firing；通知保留原因，不把动作写成已执行。"""

    if not str(session_id or "").strip():
        return ""
    from src.storage.database import get_latest_assistant_chat_message

    error = str((get_latest_assistant_chat_message(session_id) or {}).get("error") or "")
    if not error.startswith("hitl_denied:"):
        return ""
    return error.split(":", 1)[1].strip() or "此次定时任务需要人工批准，已停止；请明确重新运行"


def _assistant_result_text(session_id: str) -> str:
    if not str(session_id or "").strip():
        return ""
    from src.storage.database import get_latest_assistant_chat_message

    record = get_latest_assistant_chat_message(session_id)
    return str((record or {}).get("content") or "").strip()


def _launch_error_code(exc: ScheduledChatLaunchError) -> str:
    text = str(exc)
    lowered = text.lower()
    for code in ("skill_version_changed", "skill_context_budget_exceeded", "skill_metadata_missing", "skill_source_changed", "skill_catalog_stale", "skill_ambiguous", "skill_disabled", "skill_scope_denied", "skill_policy_invalid", "skill_unreadable", "skill_missing", "skill_unbound"):
        if code in lowered:
            return code
    if "skill" in lowered:
        return "skill_unbound"
    if "agent" in lowered:
        return "agent_missing"
    return "needs_job_update"


def _skill_context_manifest(
    job: ScheduledJob,
    *,
    validation: str,
    owner_session_id: str = "",
    firing_run_id: str = "",
    runtime: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造不含正文的 firing Skill Context 审计摘要。

    `runtime` 是本次 firing 首轮预加载的审计：注入顺序、正文 token 估算与预算、
    Toolkit 断言、Run snapshot 恢复结果、压缩后的补回记录和被拒 viewer 调用。
    """

    items = [item.model_dump(by_alias=True) for item in list(job.required_skills or [])]
    grant_versions = [item.get("ownerGrantVersion") for item in items if item.get("ownerGrantVersion") is not None]
    manifest: dict[str, Any] = {
        "skillIds": [str(item.get("skillId") or "") for item in items],
        "items": items,
        "policy": str(job.context_policy or "fresh"),
        "validation": validation,
        "totalChars": sum(int(item.get("contentChars") or 0) for item in items),
        "estimatedTokens": sum(int(item.get("estimatedTokens") or 0) for item in items),
        "budget": {"singleSkillMaxChars": SINGLE_SKILL_MAX_CHARS, "totalMaxChars": TOTAL_SKILL_MAX_CHARS},
        "ownerSessionId": owner_session_id or next(
            (str(item.get("ownerSessionId")) for item in items if item.get("ownerSessionId")), "",
        ),
        "ownerGrantVersion": grant_versions[0] if grant_versions else None,
        "firingRunId": firing_run_id,
    }
    if runtime:
        manifest["runtime"] = {key: runtime[key] for key in _RUNTIME_SKILL_AUDIT_KEYS if key in runtime}
        if runtime.get("validation") == "failed":
            manifest.update({
                "validation": "failed",
                "errorCode": str(runtime.get("errorCode") or ""),
                "errorMessage": str(runtime.get("errorMessage") or ""),
            })
    return manifest
