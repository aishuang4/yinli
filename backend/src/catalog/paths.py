"""Catalog 约定路径。运行时从 `runtime_paths.tenant_root()` 现取，按租户隔离且便于测试替换。"""

from __future__ import annotations

import os
import re
from pathlib import Path

from src.config import runtime_paths

WORKSPACES_DIRNAME = "workspaces"
SYSTEM_WORKSPACE_DIRNAME = "_system"
AGENTS_DIRNAME = ".agents"
PROFILES_DIRNAME = "profiles"
MCP_DIRNAME = "mcp"
HTTP_TOOLS_DIRNAME = "tools"
JOBS_DIRNAME = "jobs"
INDEX_FILENAME = "INDEX.md"
GITIGNORE_FILENAME = ".gitignore"
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
MAX_PROFILE_BYTES = 256 * 1024


def workspaces_root() -> Path:
    """Workspace catalog 根：当前租户 `<tenant>/workspaces`。"""

    return runtime_paths.workspaces_catalog_root()


def validate_workspace_id(workspace_id: str) -> str:
    """校验 Workspace id；`_system` 不是合法 Workspace id。"""

    value = str(workspace_id or "").strip()
    if not ID_PATTERN.match(value):
        raise ValueError(f"非法 workspace_id: {workspace_id!r}")
    if value == SYSTEM_WORKSPACE_DIRNAME:
        raise ValueError("禁止把 _system 当作 Workspace id")
    return value


def validate_agent_id(agent_id: str) -> str:
    """校验 Agent id（含 UUID 与短名）。"""

    value = str(agent_id or "").strip()
    if not ID_PATTERN.match(value):
        raise ValueError(f"非法 agent id: {agent_id!r}")
    return value


def workspace_catalog_root(workspace_id: str) -> Path:
    """`<USER_DATA_ROOT>/workspaces/<id>/.agents`。"""

    return workspaces_root() / validate_workspace_id(workspace_id) / AGENTS_DIRNAME


def profiles_dir(workspace_id: str) -> Path:
    """用户持久 Agent 目录。"""

    return workspace_catalog_root(workspace_id) / PROFILES_DIRNAME


def system_overlay_root() -> Path:
    """系统 Agent 覆盖根：`<USER_DATA_ROOT>/workspaces/_system/.agents`。"""

    return workspaces_root() / SYSTEM_WORKSPACE_DIRNAME / AGENTS_DIRNAME


def system_profiles_dir() -> Path:
    """系统 Agent 覆盖 profile 目录。"""

    return system_overlay_root() / PROFILES_DIRNAME


def mcp_dir(workspace_id: str) -> Path:
    """Workspace MCP 定义目录。"""

    return workspace_catalog_root(workspace_id) / MCP_DIRNAME


def http_tools_dir(workspace_id: str) -> Path:
    """Workspace HTTP 工具定义目录。"""

    return workspace_catalog_root(workspace_id) / HTTP_TOOLS_DIRNAME


def project_mcp_dir(project_root: Path) -> Path:
    """项目级 MCP 定义目录。"""

    return Path(project_root).expanduser().resolve() / AGENTS_DIRNAME / MCP_DIRNAME


def project_http_tools_dir(project_root: Path) -> Path:
    """项目级 HTTP 工具定义目录。"""

    return Path(project_root).expanduser().resolve() / AGENTS_DIRNAME / HTTP_TOOLS_DIRNAME


def jobs_dir(workspace_id: str) -> Path:
    """Workspace Job 定义目录。"""

    return workspace_catalog_root(workspace_id) / JOBS_DIRNAME


def project_jobs_dir(project_root: Path) -> Path:
    """项目级 Job 定义目录。"""

    return Path(project_root).expanduser().resolve() / AGENTS_DIRNAME / JOBS_DIRNAME


def profile_path(workspace_id: str, agent_id: str) -> Path:
    """用户 profile 文件路径。"""

    return profiles_dir(workspace_id) / f"{validate_agent_id(agent_id)}.md"


def system_profile_path(agent_id: str) -> Path:
    """系统 overlay 文件路径。"""

    return system_profiles_dir() / f"{validate_agent_id(agent_id)}.md"


def project_agents_dir(project_root: Path) -> Path:
    """Project `.agents` 生成物目录。"""

    return Path(project_root).expanduser().resolve() / AGENTS_DIRNAME


def project_index_path(project_root: Path) -> Path:
    """Project INDEX.md 路径。"""

    return project_agents_dir(project_root) / INDEX_FILENAME


def extra_filesystem_scopes_for_workspace(workspace_id: str) -> list[str]:
    """当前 Workspace 允许文件工具访问的 catalog 根（必须已存在）。"""

    roots = [workspace_catalog_root(workspace_id), system_overlay_root()]
    resolved: list[str] = []
    for root in roots:
        root.mkdir(parents=True, exist_ok=True)
        resolved.append(str(root.resolve()))
    return resolved


def path_stays_inside(path: Path, root: Path) -> bool:
    """resolve 后必须落在 root 内，拒绝指向根外的 symlink。"""

    try:
        return path.resolve().is_relative_to(root.resolve())
    except OSError:
        return False


def is_forbidden_extra_scope(path: Path) -> bool:
    """拒绝把 /、HOME、租户数据根本身当作 extra scope。"""

    try:
        resolved = path.expanduser().resolve()
    except OSError:
        return True
    forbidden = {
        Path(os.sep).resolve(),
        Path.home().resolve(),
        runtime_paths.tenant_root().expanduser().resolve(),
    }
    return resolved in forbidden


def atomic_write_text(path: Path, content: str) -> None:
    """写入临时文件后 os.replace，避免半截正式文件。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise
