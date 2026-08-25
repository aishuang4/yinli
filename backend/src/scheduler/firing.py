"""一次 queued firing 的执行：拿信号量后 starting，再走 start_scheduled_chat_run。"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from src.agents.runtime_types import RuntimeToolScope
from src.project_files import project_job_run_directory
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
from src.server.routes.chat.background_runs import (
    cancel_chat_background_run,
    get_chat_background_run,
)
from src.storage.database import get_project

logger = logging.getLogger(__name__)


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
            service.mark_starting(firing_id)
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
            output_manifest = _scan_outputs(job, service.get_firing(firing_id))
            if chat_status == "completed":
                terminal_status = "succeeded"
                error_code = ""
            elif chat_status == "cancelled":
                terminal_status = "failed"
                error_code = "timeout"
                error_message = "firing 等待 Chat Run 超时"
            else:
                terminal_status = "failed"
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
        logger.warning("scheduled chat launch failed firing=%s: %s", firing_id, exc)
    except Exception as exc:
        terminal_status = "failed"
        error_code = "failed"
        error_message = str(exc)
        logger.exception("run_firing failed firing=%s", firing_id)
    finally:
        ensure_firing_terminal(
            service,
            firing_id,
            status=terminal_status,
            error_code=error_code,
            error_message=error_message,
            output_manifest=output_manifest,
        )


def _build_request(job: ScheduledJob, firing: ScheduledJobRun) -> ScheduledRunRequest:
    return ScheduledRunRequest(
        job_id=job.id,
        firing_id=firing.id,
        workspace_id=job.workspace_id,
        project_id=job.project_id,
        agent_id=job.agent_id,
        frozen_prompt=job.frozen_prompt,
        skill_ids=list(job.skill_ids),
        tool_scope=runtime_tool_scope_from_job(job.tool_scope),
        scheduled_for=firing.scheduled_for,
        trigger=firing.trigger,
    )


async def _chat_session_id(chat_run_id: str) -> str:
    run = await get_chat_background_run(chat_run_id)
    return run.session_id if run is not None else ""


def _scan_outputs(job: ScheduledJob, firing: ScheduledJobRun) -> dict[str, Any]:
    project = get_project(job.project_id)
    if project is None:
        return {}
    root = Path(str(project.get("rootPath") or "")).expanduser()
    if not str(root):
        return {}
    try:
        workspace = project_job_run_directory(root, job.id, firing.id)
        return scan_scheduled_run_outputs(workspace)
    except (OSError, ValueError):
        return {}


def _launch_error_code(exc: ScheduledChatLaunchError) -> str:
    text = str(exc)
    lowered = text.lower()
    if "skill" in lowered:
        return "skill_unbound"
    if "agent" in lowered:
        return "agent_missing"
    if "project" in lowered or "workspace" in lowered:
        return "project_archived"
    return "needs_job_update"
