"""Catalog 约定路径。运行时从 `runtime_paths.tenant_root()` 现取，按租户隔离且便于测试替换。"""

from __future__ import annotations

import os
import re
from pathlib import Path

from src.config import runtime_paths

SYSTEM_OVERLAY_DIRNAME = "_system"
AGENTS_DIRNAME = ".agents"
PROFILES_DIRNAME = "profiles"
MCP_DIRNAME = "mcp"
HTTP_TOOLS_DIRNAME = "tools"
JOBS_DIRNAME = "jobs"
INDEX_FILENAME = "INDEX.md"
GITIGNORE_FILENAME = ".gitignore"
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
MAX_PROFILE_BYTES = 256 * 1024


def validate_agent_id(agent_id: str) -> str:
    """校验 Agent id（含 UUID 与短名）。"""

    value = str(agent_id or "").strip()
    if not ID_PATTERN.match(value):
        raise ValueError(f"非法 agent id: {agent_id!r}")
    return value


def profiles_dir(*_unused: object) -> Path:
    """用户持久 Agent 目录：`<tenant>/agents/profiles`。"""

    del _unused
    return runtime_paths.agents_root() / PROFILES_DIRNAME


def system_overlay_root() -> Path:
    """系统 Agent 覆盖根：`<tenant>/agents/_system`。"""

    return runtime_paths.agents_root() / SYSTEM_OVERLAY_DIRNAME


def system_profiles_dir() -> Path:
    """系统 Agent 覆盖 profile 目录。"""

    return system_overlay_root() / PROFILES_DIRNAME


def mcp_dir(*_unused: object) -> Path:
    """租户 MCP YAML 目录：`<tenant>/mcp`。"""

    del _unused
    return runtime_paths.tenant_root() / MCP_DIRNAME


def http_tools_dir(*_unused: object) -> Path:
    """租户 HTTP 工具 YAML 目录：`<tenant>/tools`。"""

    del _unused
    return runtime_paths.tenant_root() / HTTP_TOOLS_DIRNAME


def jobs_dir(*_unused: object) -> Path:
    """租户 Job 定义目录：`<tenant>/agents/jobs`。"""

    del _unused
    return runtime_paths.agents_root() / JOBS_DIRNAME


def profile_path(*parts: str) -> Path:
    """用户 profile 文件路径。多段参数时只取最后一段作为 agent id。"""

    if not parts:
        raise ValueError("缺少 agent id")
    return profiles_dir() / f"{validate_agent_id(parts[-1])}.md"


def system_profile_path(agent_id: str) -> Path:
    """系统 overlay 文件路径。"""

    return system_profiles_dir() / f"{validate_agent_id(agent_id)}.md"


def project_agents_dir(project_root: Path) -> Path:
    """Project `.agents` 生成物目录。"""

    return Path(project_root).expanduser().resolve() / AGENTS_DIRNAME


def project_index_path(project_root: Path) -> Path:
    """Project INDEX.md 路径。"""

    return project_agents_dir(project_root) / INDEX_FILENAME


def extra_filesystem_scopes_for_workspace(*_unused: object) -> list[str]:
    """文件工具可访问的 Agent 目录与系统 overlay（必须已存在）。传入参数不再过滤。"""

    del _unused
    roots = [runtime_paths.agents_root(), system_overlay_root()]
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
