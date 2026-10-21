"""定时任务定义 Markdown：frontmatter + frozen_prompt 正文。

定义只来自租户 `agents/jobs`，不扫描授权目录下的 `.agents/jobs`。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from src.catalog.models import CatalogError
from src.catalog.paths import (
    ID_PATTERN,
    MAX_PROFILE_BYTES,
    atomic_write_text,
    jobs_dir,
    path_stays_inside,
)
from src.catalog.system_defaults import utc_now

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(?P<yaml>.*?)\n---\s*\n?(?P<body>.*)\Z", re.DOTALL)
DEFAULT_TOOL_SCOPE = {
    "allow_bash": False,
    "allow_http": False,
    "allow_web": False,
    "allow_mcp": False,
    "allow_knowledge": False,
}


@dataclass
class JobFileRecord:
    id: str
    name: str
    project_id: str
    agent_id: str
    frozen_prompt: str
    mode: str = "agent"
    script_filename: str = ""
    script_source: str = ""
    interpreter: str = "python3"
    script_args: list[str] = field(default_factory=list)
    timeout_seconds: int = 300
    schedule_kind: str = "weekly"
    schedule_expr: dict[str, Any] = field(default_factory=dict)
    timezone: str = "Asia/Shanghai"
    desired_status: str = "paused"
    summary: str = ""
    skill_ids: list[str] = field(default_factory=list)
    tool_scope: dict[str, bool] = field(default_factory=lambda: dict(DEFAULT_TOOL_SCOPE))
    missed_policy: str = "catch_up_once"
    failure_policy: str = "notify_keep_schedule"
    delivery_policy: str = "notify"
    task_intent: str = ""
    source_scope: str = "agents"
    source_path: str = ""


def dump_job_markdown(record: JobFileRecord) -> str:
    frontmatter = {
        "id": record.id,
        "name": record.name,
        "project_id": record.project_id,
        "agent_id": record.agent_id,
        "mode": record.mode if record.mode in {"agent", "script"} else "agent",
        "schedule_kind": record.schedule_kind,
        "schedule_expr": dict(record.schedule_expr),
        "timezone": record.timezone,
        "desired_status": record.desired_status if record.desired_status in {"active", "paused"} else "paused",
        "summary": record.summary,
        "skill_ids": list(record.skill_ids),
        "tool_scope": {**DEFAULT_TOOL_SCOPE, **dict(record.tool_scope or {})},
        "missed_policy": record.missed_policy,
        "failure_policy": record.failure_policy,
        "delivery_policy": record.delivery_policy,
        "task_intent": record.task_intent or record.name,
        "updated_at": utc_now(),
    }
    if frontmatter["mode"] == "script":
        frontmatter["script_filename"] = record.script_filename
        frontmatter["interpreter"] = record.interpreter
        frontmatter["args"] = list(record.script_args)
        frontmatter["timeout_seconds"] = int(record.timeout_seconds)
    dumped = yaml.safe_dump(frontmatter, allow_unicode=True, default_flow_style=False, sort_keys=False).strip()
    body_source = record.script_source if frontmatter["mode"] == "script" else record.frozen_prompt
    body = (body_source or "").rstrip() + "\n"
    return f"---\n{dumped}\n---\n\n{body}"


def parse_job_markdown(
    text: str,
    *,
    path: Path,
    project_id: str,
    source_scope: str,
    **_extra: object,
) -> JobFileRecord:
    """解析 Job Markdown。frontmatter 里的未知键忽略，不因此失败。"""

    del _extra
    if len(text.encode("utf-8")) > MAX_PROFILE_BYTES:
        raise ValueError(f"Job 文件超过 {MAX_PROFILE_BYTES} 字节")
    match = _FRONTMATTER_RE.match(text)
    if not match:
        raise ValueError("Job 文件缺少 YAML frontmatter")
    payload = yaml.safe_load(match.group("yaml")) or {}
    if not isinstance(payload, dict):
        raise ValueError("Job frontmatter 必须是对象")
    job_id = str(payload.get("id") or path.stem).strip()
    if not ID_PATTERN.match(job_id) or path.stem != job_id:
        raise ValueError(f"Job id 与文件名不一致: {path.name}")
    name = str(payload.get("name") or "").strip()
    mode = str(payload.get("mode") or "agent").strip() or "agent"
    if mode not in {"agent", "script"}:
        raise ValueError("Job mode 必须为 agent 或 script")
    agent_id = str(payload.get("agent_id") or payload.get("agentId") or "").strip()
    prompt = match.group("body").strip()
    if not name:
        raise ValueError("Job 文件必须包含 name")
    if mode == "agent" and not agent_id:
        raise ValueError("Job 文件必须包含 name 与 agent_id")
    if not prompt:
        raise ValueError("脚本正文不能为空" if mode == "script" else "Job 正文 frozen_prompt 不能为空")
    script_filename = str(payload.get("script_filename") or payload.get("scriptFilename") or "").strip()
    interpreter = str(payload.get("interpreter") or "python3").strip() or "python3"
    raw_args = payload.get("args") or []
    script_args = [str(item) for item in raw_args] if isinstance(raw_args, list) else []
    try:
        timeout_seconds = int(payload.get("timeout_seconds") or payload.get("timeoutSeconds") or 300)
    except (TypeError, ValueError):
        timeout_seconds = 300
    desired = str(payload.get("desired_status") or payload.get("desiredStatus") or "paused").strip()
    if desired not in {"active", "paused"}:
        desired = "paused"
    expr = payload.get("schedule_expr") or payload.get("scheduleExpr") or {}
    if not isinstance(expr, dict):
        expr = {}
    scope = payload.get("tool_scope") or payload.get("toolScope") or {}
    if not isinstance(scope, dict):
        scope = {}
    return JobFileRecord(
        id=job_id,
        name=name,
        project_id=str(payload.get("project_id") or payload.get("projectId") or project_id),
        agent_id=agent_id,
        frozen_prompt="" if mode == "script" else prompt,
        mode=mode,
        script_filename=script_filename,
        script_source=prompt if mode == "script" else "",
        interpreter=interpreter,
        script_args=script_args,
        timeout_seconds=timeout_seconds,
        schedule_kind=str(payload.get("schedule_kind") or payload.get("scheduleKind") or "weekly"),
        schedule_expr=dict(expr),
        timezone=str(payload.get("timezone") or "Asia/Shanghai"),
        desired_status=desired,
        summary=str(payload.get("summary") or ""),
        skill_ids=[str(item) for item in list(payload.get("skill_ids") or payload.get("skillIds") or [])],
        tool_scope={**DEFAULT_TOOL_SCOPE, **{str(key): bool(value) for key, value in scope.items()}},
        missed_policy=str(payload.get("missed_policy") or payload.get("missedPolicy") or "catch_up_once"),
        failure_policy=str(payload.get("failure_policy") or payload.get("failurePolicy") or "notify_keep_schedule"),
        delivery_policy=str(payload.get("delivery_policy") or payload.get("deliveryPolicy") or "notify"),
        task_intent=str(payload.get("task_intent") or payload.get("taskIntent") or name),
        source_scope=source_scope,
        source_path=str(path),
    )


def write_job_file(path: Path, record: JobFileRecord) -> None:
    atomic_write_text(path, dump_job_markdown(record))


def _scan_dir(
    directory: Path,
    *,
    project_id: str,
    source_scope: str,
) -> tuple[list[JobFileRecord], list[CatalogError]]:
    if not directory.is_dir():
        return [], []
    records: list[JobFileRecord] = []
    errors: list[CatalogError] = []
    for path in sorted(directory.glob("*.md")):
        if path.name.startswith("."):
            continue
        if not path_stays_inside(path, directory):
            errors.append(CatalogError(
                path=str(path),
                message="symlink 或解析路径指向 catalog 根外",
                agent_id=path.stem,
            ))
            continue
        try:
            records.append(parse_job_markdown(
                path.read_text(encoding="utf-8"),
                path=path,
                project_id=project_id,
                source_scope=source_scope,
            ))
        except (OSError, ValueError, yaml.YAMLError) as exc:
            errors.append(CatalogError(path=str(path), message=str(exc), agent_id=path.stem))
    return records, errors


def scan_job_files(
    *,
    project_root: Path | str | None = None,
    project_id: str = "default",
    **_extra: object,
) -> tuple[list[JobFileRecord], list[CatalogError]]:
    """只扫描租户 agents/jobs。项目目录参数保留给旧调用，不再读取。"""

    del project_root, _extra
    return _scan_dir(jobs_dir(), project_id=project_id, source_scope="agents")


def discover_job_files(
    *,
    project_root: Path | str | None = None,
    project_id: str = "default",
    **_extra: object,
) -> list[JobFileRecord]:
    records, _errors = scan_job_files(project_root=project_root, project_id=project_id, **_extra)
    return records


def job_definition_path(*, project_id: str = "", job_id: str, **_extra: object) -> Path:
    """服务写入租户 agents/jobs，避免把运行实例写进用户目录。"""

    del project_id, _extra
    return jobs_dir() / f"{job_id}.md"


def overlay_instance_with_file(record: dict[str, Any], file: JobFileRecord) -> dict[str, Any]:
    """用定义文件覆盖 sqlite 派生缓存列。"""

    overlaid = dict(record)
    overlaid.update({
        "name": file.name,
        "summary": file.summary,
        "agent_id": file.agent_id,
        "mode": file.mode,
        "frozen_prompt": file.frozen_prompt,
        "input_spec": {
            **dict(record.get("input_spec") or {}),
            **(
                {
                    "script_filename": file.script_filename,
                    "script_source": file.script_source,
                    "interpreter": file.interpreter,
                    "args": list(file.script_args),
                    "timeout_seconds": file.timeout_seconds,
                }
                if file.mode == "script"
                else {}
            ),
        },
        "schedule_kind": file.schedule_kind,
        "schedule_expr": dict(file.schedule_expr),
        "timezone": file.timezone,
        "skill_ids": list(file.skill_ids),
        "tool_scope": dict(file.tool_scope),
        "missed_policy": file.missed_policy,
        "failure_policy": file.failure_policy,
        "delivery_policy": file.delivery_policy,
    })
    return overlaid


def load_job_definition_file(
    *,
    job_id: str,
    project_id: str = "",
    **_extra: object,
) -> JobFileRecord | None:
    """按 id 读取租户 agents/jobs 下的定义文件。"""

    del _extra
    if not job_id:
        return None
    path = job_definition_path(project_id=project_id, job_id=job_id)
    if not path.is_file() or not path_stays_inside(path, path.parent):
        return None
    try:
        return parse_job_markdown(
            path.read_text(encoding="utf-8"),
            path=path,
            project_id=project_id,
            source_scope="agents",
        )
    except (OSError, ValueError, yaml.YAMLError):
        return None


def job_source_path_index(
    *,
    project_root: Path | str | None = None,
    project_id: str = "default",
    **_extra: object,
) -> dict[str, str]:
    """id -> 定义文件绝对路径，供 Jobs 页门面展示。"""

    return {
        item.id: item.source_path
        for item in discover_job_files(
            project_root=project_root,
            project_id=project_id,
            **_extra,
        )
        if item.source_path
    }
