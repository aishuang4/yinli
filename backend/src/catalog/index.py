"""生成 Project `.agents/INDEX.md`。"""

from __future__ import annotations

import logging
from pathlib import Path

from src.agents.system_agents import is_system_agent_id
from src.catalog.loader import catalog_fingerprint, ensure_loaded, list_workspace_agents
from src.catalog.paths import (
    GITIGNORE_FILENAME,
    atomic_write_text,
    extra_filesystem_scopes_for_workspace,
    project_agents_dir,
    project_index_path,
    system_profile_path,
    workspace_catalog_root,
)
from src.catalog.system_defaults import utc_now

logger = logging.getLogger(__name__)

INDEX_GITIGNORE_LINE = "INDEX.md"


def _skill_index_rows(project_root: Path, workspace_id: str) -> list[list[str]]:
    from src.catalog.skill_seed import seed_system_skills
    from src.catalog.skills import discover_skill_packages, list_system_skill_packages
    from src.config.runtime_paths import tenant_root, user_home_root

    seed_system_skills()
    rows: list[list[str]] = []
    for item in list_system_skill_packages():
        rows.append([item.name, item.resource_path, "true", item.source_scope])
    for item in discover_skill_packages(
        Path(project_root),
        user_data_root=tenant_root(),
        user_home_root=user_home_root(),
        workspace_id=workspace_id,
    ):
        rows.append([item.name, item.resource_path, "true" if item.enabled else "false", item.source_scope])
    return rows


def _mcp_index_rows(project_root: Path, workspace_id: str) -> list[list[str]]:
    from src.catalog.mcp_files import discover_mcp_files

    del workspace_id
    return [
        [item.name, item.source_path, "true" if item.enabled else "false", item.source_scope]
        for item in discover_mcp_files(project_root=project_root)
    ]


def _http_index_rows(project_root: Path, workspace_id: str) -> list[list[str]]:
    from src.catalog.http_files import discover_http_files

    return [
        [item.name, item.source_path, "true" if item.enabled else "false", item.source_scope]
        for item in discover_http_files(project_root=project_root)
    ]


def _job_index_rows(project_root: Path, workspace_id: str, project_id: str):
    from src.catalog.job_files import scan_job_files
    from src.catalog.sync_jobs import sync_job_definitions

    try:
        sync_job_definitions(project_root=project_root, project_id=project_id or "default")
    except Exception:
        pass
    records, errors = scan_job_files(
        project_root=project_root,
        project_id=project_id or "default",
        workspace_id=workspace_id or "default",
    )
    rows = [
        [item.id, item.name, item.desired_status, item.source_path]
        for item in records
    ]
    return rows, errors


def _ensure_gitignore(directory: Path) -> None:
    gitignore = directory / GITIGNORE_FILENAME
    existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    lines = existing.splitlines()
    if INDEX_GITIGNORE_LINE in {line.strip() for line in lines}:
        return
    suffix = "" if existing.endswith("\n") or not existing else "\n"
    gitignore.write_text(existing + suffix + INDEX_GITIGNORE_LINE + "\n", encoding="utf-8")


def _markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    head = "| " + " | ".join(headers) + " |"
    sep = "| " + " | ".join("---" for _ in headers) + " |"
    body = "\n".join("| " + " | ".join(row) + " |" for row in rows) if rows else "| （无） |"
    if not rows:
        return "\n".join([head, sep, "| " + " | ".join("（无）" for _ in headers) + " |"])
    return "\n".join([head, sep, body])


def render_index_markdown(
    *,
    workspace_id: str,
    project_id: str,
    project_root: Path,
    docker: bool = False,
) -> str:
    """渲染 INDEX 正文。"""

    snapshot = ensure_loaded()
    catalog_root = workspace_catalog_root(workspace_id).resolve()
    system_rows: list[list[str]] = []
    profile_rows: list[list[str]] = []
    for agent in list_workspace_agents(workspace_id):
        if is_system_agent_id(agent.id):
            overlay = system_profile_path(agent.id)
            overlay_cell = str(overlay.resolve()) if overlay.exists() else "（代码内置，创建 overlay 即生效）"
            system_rows.append([agent.id, agent.name, agent.role, overlay_cell, "身份只读"])
        else:
            profile_rows.append([agent.id, agent.name, agent.role, agent.source_path or "（无路径）"])
    skill_rows = _skill_index_rows(project_root, workspace_id)
    mcp_rows = _mcp_index_rows(project_root, workspace_id)
    http_rows = _http_index_rows(project_root, workspace_id)
    job_rows, job_errors = _job_index_rows(project_root, workspace_id, project_id)
    errors = list(snapshot.errors) + list(job_errors)
    error_block = "（无）" if not errors else "\n".join(
        f"- `{item.path}`: {item.message}" for item in errors
    )
    warning_block = "（无）" if not snapshot.warnings else "\n".join(
        f"- {item}" for item in snapshot.warnings
    )
    how = [
        "- 了解系统：只读本文件。",
        "- 修改 Agent：编辑「持久 Agent」表中的 path。",
        "- 修改 Skill：编辑或新建项目 `.agents/skills/<name>/SKILL.md`。",
        "- 修改 MCP/HTTP：编辑 catalog_root 下 `mcp/<id>.yaml` 或 `tools/<id>.yaml`，不要写入 Token。",
        "- 修改定时任务：编辑或新建 `.agents/jobs/<id>.md`；desired_status=active 才会调度。",
        "- 不要修改 models.json、密钥目录、本 INDEX（下次运行会覆盖）。",
        "- 不要把 Chat Agent 写到 .agents/agents/（那是 Subagent）。",
    ]
    if docker:
        how.append("- 当前是 Docker 沙箱，持久 Agent 文件只读投影，修改请改用本地执行或到 Agents 页面。")
    return "\n".join([
        "# YINLI Catalog",
        "",
        f"- workspace: {workspace_id}",
        f"- project: {project_id}",
        f"- project_root: {project_root.resolve()}",
        f"- catalog_root: {catalog_root}",
        f"- generated_at: {utc_now()}",
        f"- fingerprint: {catalog_fingerprint()}",
        "",
        "## 如何操作系统",
        "",
        *how,
        "",
        "## 系统 Agent（身份只读）",
        "",
        _markdown_table(["id", "name", "role", "overlay_path", "notes"], system_rows),
        "",
        "## 持久 Agent",
        "",
        _markdown_table(["id", "name", "role", "path"], profile_rows),
        "",
        "## Skills",
        "",
        _markdown_table(["name", "path", "enabled", "source"], skill_rows),
        "",
        "## MCP",
        "",
        _markdown_table(["name", "path", "enabled", "source"], mcp_rows),
        "",
        "## HTTP 工具",
        "",
        _markdown_table(["name", "path", "enabled", "source"], http_rows),
        "",
        "## Jobs",
        "",
        _markdown_table(["id", "name", "desired_status", "path"], job_rows),
        "",
        "## 解析错误",
        "",
        error_block,
        "",
        "## 警告",
        "",
        warning_block,
        "",
    ])


def refresh_project_index(
    project_root: Path,
    *,
    workspace_id: str,
    project_id: str = "",
    docker: bool = False,
) -> Path | None:
    """写入 INDEX.md。不可写时返回 None，不抛到 Chat。"""

    extra_filesystem_scopes_for_workspace(workspace_id)
    target = project_index_path(project_root)
    try:
        directory = project_agents_dir(project_root)
        directory.mkdir(parents=True, exist_ok=True)
        _ensure_gitignore(directory)
        markdown = render_index_markdown(
            workspace_id=workspace_id,
            project_id=project_id or "",
            project_root=Path(project_root),
            docker=docker,
        )
        atomic_write_text(target, markdown)
        return target
    except OSError as exc:
        logger.warning("无法写入 %s: %s", target, exc)
        return None
