"""定时 firing 与 Chat Run 的领域桥接。

调度器必须走本模块，禁止直接 await start_chat_run()。
执行面经 build_scheduled_run_context 裁剪无人值守工具。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

from fastapi import HTTPException

from src.agents.runtime_types import RuntimeToolScope
from src.project_files import project_job_run_directory
from src.runtime.scheduled_chat import (
    TERMINAL_RUN_STATUSES,
    assert_job_workspace_in_scope,
    cancel_chat_background_run,
    get_chat_background_run,
    launch_scheduled_chat_run,
)
from src.runtime_context import resolve_runtime_context
from src.server.routes.agents import get_registered_agent


DEFAULT_FIRING_TIMEOUT_SECONDS = 900
_POLL_INTERVAL_SECONDS = 0.05


class ScheduledChatLaunchError(Exception):
    """领域错误，不抛 HTTPException。"""


@dataclass(frozen=True)
class ScheduledRunRequest:
    """内部 DTO，禁止出现在公开 ChatMessage / HTTP schema。"""

    job_id: str
    firing_id: str
    workspace_id: str
    project_id: str
    agent_id: str
    frozen_prompt: str
    skill_ids: list[str]
    tool_scope: RuntimeToolScope
    scheduled_for: str
    trigger: Literal["schedule", "manual", "catch_up"]
    model_config_id: str = ""


def scheduled_chat_run_timeout_seconds(agent_id: str) -> float:
    """硬超时 min(agent.timeout_seconds, 900)。"""

    timeout = 300
    agent = get_registered_agent(agent_id)
    if agent is not None:
        timeout = int(getattr(agent.guardrails, "timeout_seconds", 300) or 300)
    return float(min(max(1, timeout), DEFAULT_FIRING_TIMEOUT_SECONDS))


def scan_scheduled_run_outputs(workspace_dir: Path | str) -> dict[str, Any]:
    """扫描 firing cwd 的 outputs/，生成 sha256 / 字节数 / 相对路径清单。"""

    root = Path(workspace_dir).expanduser().resolve()
    outputs = (root / "outputs").resolve()
    files: list[dict[str, Any]] = []
    if outputs.is_dir() and outputs.is_relative_to(root):
        for candidate in sorted(outputs.rglob("*")):
            if not candidate.is_file() or candidate.is_symlink():
                continue
            resolved = candidate.resolve()
            if not resolved.is_relative_to(outputs) or not resolved.is_relative_to(root):
                continue
            digest, size = _sha256_file(resolved)
            files.append({
                "path": resolved.relative_to(root).as_posix(),
                "sha256": digest,
                "bytes": size,
            })
    manifest = {"files": files}
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest


async def start_scheduled_chat_run(req: ScheduledRunRequest) -> str:
    """创建 kind=scheduled_job 的新 session 与 Chat Run，立即返回 chat_run_id。"""

    try:
        return await _start_scheduled_chat_run(req)
    except ScheduledChatLaunchError:
        raise
    except HTTPException as exc:
        raise ScheduledChatLaunchError(str(exc.detail)) from exc
    except ValueError as exc:
        raise ScheduledChatLaunchError(str(exc)) from exc


async def await_scheduled_chat_run(
    chat_run_id: str, *, timeout_seconds: float,
) -> str:
    """等待 chat_runs 进入 completed/failed/cancelled；超时则取消并返回 cancelled。"""

    timeout = min(max(0.0, float(timeout_seconds)), DEFAULT_FIRING_TIMEOUT_SECONDS)
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        run = await get_chat_background_run(chat_run_id)
        if run is None:
            raise ScheduledChatLaunchError(f"找不到 Chat Run: {chat_run_id}")
        if run.status in TERMINAL_RUN_STATUSES:
            await _join_background_task(run)
            return run.status
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            task = run.async_task
            cancelled = await cancel_chat_background_run(chat_run_id)
            await _join_background_task(cancelled or run, task=task)
            final = await get_chat_background_run(chat_run_id)
            if final is not None:
                return final.status
            return cancelled.status if cancelled is not None else "cancelled"
        await asyncio.sleep(min(_POLL_INTERVAL_SECONDS, remaining))


async def _start_scheduled_chat_run(req: ScheduledRunRequest) -> str:
    _validate_request(req)
    from src.config.runtime_settings import resolve_model_binding
    binding = resolve_model_binding(req.model_config_id)
    session_id = f"sched-{uuid.uuid4().hex}"
    runtime_context = resolve_runtime_context(
        project_id=req.project_id,
        session_id=session_id,
        run_id="",
        agent_id=req.agent_id,
    )
    if runtime_context.workspace_id != req.workspace_id:
        raise ScheduledChatLaunchError(
            f"Job Workspace {req.workspace_id} 与 Project {runtime_context.project_id} 不一致",
        )
    agent = get_registered_agent(req.agent_id)
    if req.agent_id != "default" and agent is None:
        raise ScheduledChatLaunchError(f"Agent 配置不存在: {req.agent_id}")
    agent_name = agent.name if agent is not None else "Assistant"

    workspace_dir = project_job_run_directory(
        runtime_context.project_root, req.job_id, req.firing_id,
    )
    assert_job_workspace_in_scope(
        agent=agent,
        project_root=runtime_context.project_root,
        workspace_dir=workspace_dir,
    )
    (workspace_dir / "outputs").mkdir(parents=True, exist_ok=True)
    _write_frozen_spec(workspace_dir, req)
    return await launch_scheduled_chat_run(
        session_id=session_id,
        agent_id=req.agent_id,
        agent_name=agent_name,
        frozen_prompt=req.frozen_prompt,
        skill_ids=list(req.skill_ids),
        workspace_id=runtime_context.workspace_id,
        project_id=runtime_context.project_id,
        workspace_dir=workspace_dir,
        runtime_context=runtime_context,
        job=_job_view(req),
        firing=_firing_view(req),
        model_binding=binding,
        job_id=req.job_id,
        firing_id=req.firing_id,
    )


def _job_view(req: ScheduledRunRequest) -> SimpleNamespace:
    from src.scheduler.service import ScheduledJobService

    try:
        job = ScheduledJobService().get_job(req.job_id)
        return job
    except Exception:
        return SimpleNamespace(
            id=req.job_id,
            agent_id=req.agent_id,
            frozen_prompt=req.frozen_prompt,
            skill_ids=list(req.skill_ids),
            tool_scope=req.tool_scope,
            capability_contract=None,
            workspace_id=req.workspace_id,
            project_id=req.project_id,
        )


def _firing_view(req: ScheduledRunRequest) -> SimpleNamespace:
    return SimpleNamespace(id=req.firing_id, job_id=req.job_id)


def _validate_request(req: ScheduledRunRequest) -> None:
    if not str(req.job_id).strip() or not str(req.firing_id).strip():
        raise ScheduledChatLaunchError("job_id 与 firing_id 不能为空")
    if not str(req.frozen_prompt).strip():
        raise ScheduledChatLaunchError("frozen_prompt 不能为空")
    if not str(req.agent_id).strip():
        raise ScheduledChatLaunchError("agent_id 不能为空")
    if not str(req.project_id).strip():
        raise ScheduledChatLaunchError("project_id 不能为空")
    if req.trigger not in {"schedule", "manual", "catch_up"}:
        raise ScheduledChatLaunchError(f"非法 firing trigger: {req.trigger}")


async def _join_background_task(run, *, task: asyncio.Task | None = None) -> None:
    """取消后等消费协程收口，避免 scan outputs/ 与尚未落盘的写入竞态。"""

    pending = task if task is not None else getattr(run, "async_task", None)
    if pending is None or pending.done():
        return
    await asyncio.gather(pending, return_exceptions=True)


def _write_frozen_spec(workspace_dir: Path, req: ScheduledRunRequest) -> None:
    payload = {
        "jobId": req.job_id,
        "firingId": req.firing_id,
        "agentId": req.agent_id,
        "projectId": req.project_id,
        "workspaceId": req.workspace_id,
        "scheduledFor": req.scheduled_for,
        "trigger": req.trigger,
        "skillIds": list(req.skill_ids),
        "toolScope": {
            "allowBash": req.tool_scope.allow_bash,
            "allowHttp": req.tool_scope.allow_http,
            "allowWeb": req.tool_scope.allow_web,
            "allowMcp": req.tool_scope.allow_mcp,
            "allowKnowledge": req.tool_scope.allow_knowledge,
        },
        "frozenPrompt": req.frozen_prompt,
    }
    (workspace_dir / "spec.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size
