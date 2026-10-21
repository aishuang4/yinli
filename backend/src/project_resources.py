"""Project 目录授权与文件工具范围。

能力不再按 Project binding 过滤：已启用且扫描到的 Skill、租户全局 MCP / HTTP
都可用。本模块只回答文件工具能读、能写哪些目录。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from src.agents.system_agents import is_system_agent_id
from src.config.runtime_paths import session_root, tenant_root
from src.runtime_context import FolderGrant, RuntimeContext

_READ_MODES = frozenset({"read", "readwrite"})
_WRITE_MODES = frozenset({"readwrite"})
# 默认项目不能写到这些租户系统目录。
_PROTECTED_DIR_NAMES = frozenset({"data", "agents", "skills", "backups", "mcp", "tools"})


class DirectoryGrantError(ValueError):
    """目录授权无效。消息以 directory_grant_invalid 开头。"""

    code = "directory_grant_invalid"

    def __init__(self, message: str) -> None:
        super().__init__(f"directory_grant_invalid: {message}")


@dataclass(frozen=True)
class ProjectResourceAccess:
    """本轮目录授权。Skill / MCP / HTTP 不在这里裁剪。"""

    project_slug: str
    session_id: str
    project_root: Path
    folders: tuple[FolderGrant, ...] = ()

    def require_agent(self, agent_id: str) -> None:
        """系统 Agent 与 group-studio-host 不要求租户内注册。"""

        if is_system_agent_id(agent_id) or agent_id in {"default", "group-studio-host"}:
            return
        from src.server.routes.agents import get_registered_agent

        if get_registered_agent(agent_id) is None:
            raise ValueError(f"Agent 配置不存在: {agent_id}")


def load_project_access(context: RuntimeContext) -> ProjectResourceAccess:
    """从运行上下文取出目录授权，不查绑定表。"""

    return ProjectResourceAccess(
        project_slug=context.project_slug,
        session_id=context.session_id,
        project_root=context.project_root,
        folders=context.folders,
    )


def path_is_within(path: Path, root: Path) -> bool:
    """realpath 之后做前缀比较，前缀后必须是分隔符。/a/b 不能匹配 /a/bc。"""

    candidate = os.path.realpath(path)
    base = os.path.realpath(root)
    if candidate == base:
        return True
    prefix = base if base.endswith(os.sep) else base + os.sep
    return candidate.startswith(prefix)


def assert_granted_path(
    raw: str | Path,
    *,
    roots: list[Path],
    base: Path | None = None,
) -> Path:
    """校验路径落在授权目录内。

    路径段含 ``..`` 直接拒绝。任一中间层是符号链接且 realpath 跑出授权目录也拒绝。
    """

    if not roots:
        raise DirectoryGrantError("没有授权目录")
    path = Path(os.fspath(raw)).expanduser()
    if ".." in path.parts:
        raise DirectoryGrantError("路径不能包含 ..")

    if path.is_absolute():
        current = Path(path.anchor)
        parts = path.parts[1:]
    else:
        if base is None:
            raise DirectoryGrantError("相对路径缺少基准目录")
        current = Path(os.path.realpath(base))
        parts = path.parts

    for part in parts:
        if part in {"", "."}:
            continue
        if part == "..":
            raise DirectoryGrantError("路径不能包含 ..")
        nxt = current / part
        try:
            linked = nxt.is_symlink()
        except OSError as exc:
            raise DirectoryGrantError(f"无法检查路径: {nxt}") from exc
        if linked:
            resolved = Path(os.path.realpath(nxt))
            if not any(path_is_within(resolved, root) for root in roots):
                raise DirectoryGrantError(f"符号链接跑出授权目录: {nxt}")
            current = resolved
            continue
        current = nxt

    resolved = Path(os.path.realpath(current))
    if not any(path_is_within(resolved, root) for root in roots):
        raise DirectoryGrantError(f"路径不在授权目录内: {resolved}")
    return resolved


def intersect_directory(folder: Path, agent_scope: Path) -> Path | None:
    """Project 目录与 Agent filesystem scope 的交集。不相交返回 None。"""

    try:
        folder_real = Path(os.path.realpath(folder))
        agent_real = Path(os.path.realpath(agent_scope))
    except OSError:
        return None
    if path_is_within(folder_real, agent_real):
        return folder_real
    if path_is_within(agent_real, folder_real):
        return agent_real
    return None


def grant_roots(context: RuntimeContext, agent_scope: Path) -> tuple[list[Path], list[Path]]:
    """返回 (可读根, 可写根)。可读含 read 与 readwrite，可写只含 readwrite。"""

    readable: list[Path] = []
    writable: list[Path] = []
    for folder in context.folders:
        if folder.access_mode not in _READ_MODES:
            continue
        hit = intersect_directory(Path(folder.path), agent_scope)
        if hit is None:
            continue
        readable.append(hit)
        if folder.access_mode in _WRITE_MODES and _write_root_allowed(context, hit):
            writable.append(hit)
    session = _session_directory(context.session_id)
    if session is not None:
        readable.append(session)
        if _write_root_allowed(context, session):
            writable.append(session)
    if not readable and not writable:
        raise DirectoryGrantError(
            f"Agent filesystem scope {agent_scope} 与 Project {context.project_slug} 的目录授权不相交。",
        )
    return _unique_paths(readable), _unique_paths(writable)


def _write_root_allowed(context: RuntimeContext, path: Path) -> bool:
    """默认项目只能写 projects/<slug>/ 与 sessions/<id>/。"""

    if context.project_slug != "default":
        return True
    return _within_default_write_tree(path, slug=context.project_slug, session_id=context.session_id)


def _within_default_write_tree(path: Path, *, slug: str, session_id: str) -> bool:
    allowed: list[Path] = [tenant_root() / "projects" / slug]
    if session_id:
        allowed.append(session_root(session_id))
    try:
        resolved = Path(os.path.realpath(path))
    except OSError:
        return False
    if not any(path_is_within(resolved, root) for root in allowed):
        return False
    # 即使前缀碰巧套住系统目录，也不允许写 data/、agents/ 等。
    tenant = Path(os.path.realpath(tenant_root()))
    if path_is_within(resolved, tenant):
        relative = resolved.relative_to(tenant)
        if relative.parts and relative.parts[0] in _PROTECTED_DIR_NAMES:
            return False
    return True


def _session_directory(session_id: str) -> Path | None:
    if not str(session_id or "").strip():
        return None
    directory = session_root(session_id)
    directory.mkdir(parents=True, exist_ok=True)
    return Path(os.path.realpath(directory))


def _unique_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    unique: list[Path] = []
    for path in paths:
        key = os.path.realpath(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(Path(key))
    return unique
