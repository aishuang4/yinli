"""Project 运行时归属解析与上下文模型。"""
from __future__ import annotations


from dataclasses import dataclass, replace
from pathlib import Path

from src.storage.records.project_records import (
    get_chat_session_location,
    get_project,
)


@dataclass(frozen=True)
class FolderGrant:
    """一条目录授权。access_mode 为 read 或 readwrite。"""

    path: str
    access_mode: str


@dataclass(frozen=True)
class RuntimeContext:
    """一次 Runtime 不可变的 Project 归属与目录授权。"""

    project_slug: str
    project_root: Path
    session_id: str
    run_id: str
    agent_id: str
    folders: tuple[FolderGrant, ...] = ()
    project_name: str = ""
    instructions_file: str = ""

    def with_run_id(self, run_id: str) -> "RuntimeContext":
        """返回绑定后台 Run ID 的新上下文。"""

        return replace(self, run_id=run_id)


def resolve_runtime_context(
    *,
    project_slug: str | None,
    session_id: str,
    run_id: str,
    agent_id: str,
) -> RuntimeContext:
    """解析 Project。已有会话沿用其 slug；新建会话必须显式给出 slug。"""

    session_location = get_chat_session_location(session_id) if session_id else None
    requested = str(project_slug or "").strip()
    if session_location:
        session_slug = str(session_location["projectSlug"])
        if requested and requested != session_slug:
            raise ValueError(
                f"Session {session_id} 属于 Project {session_slug}，不能在 Project {requested} 中继续。",
            )
        resolved = session_slug
    else:
        if not requested:
            raise ValueError("缺少 project_slug")
        resolved = requested

    project = get_project(resolved)
    if project is None:
        raise ValueError(f"Project 不存在: {resolved}")
    if str(project.get("status") or "") != "active":
        raise ValueError(f"Project 已归档，不能启动新运行: {resolved}")

    project_root = Path(str(project.get("primaryPath") or "")).expanduser().resolve()
    if not project_root.exists() or not project_root.is_dir():
        raise ValueError(f"Project root 不是有效目录: {project_root}")

    return RuntimeContext(
        project_slug=str(project.get("slug") or resolved),
        project_root=project_root,
        folders=_folders_from_project(project, project_root),
        session_id=session_id,
        run_id=run_id,
        agent_id=agent_id,
        project_name=str(project.get("name") or resolved),
        instructions_file=str(project.get("instructionsFile") or ""),
    )


def _folders_from_project(project: dict, project_root: Path) -> tuple[FolderGrant, ...]:
    """从 get_project 的 folders 取出 path 与读写模式。"""

    grants: list[FolderGrant] = []
    for item in project.get("folders") or []:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        mode = str(item.get("access_mode") or item.get("accessMode") or "read")
        if not path:
            continue
        if mode not in {"read", "readwrite"}:
            mode = "read"
        grants.append(FolderGrant(path=path, access_mode=mode))
    if grants:
        return tuple(grants)
    return (FolderGrant(path=str(project_root), access_mode="readwrite"),)


def load_project_instructions(context: RuntimeContext, *, max_chars: int = 20_000) -> str:
    """读取 Project instructions，且拒绝越过主目录。"""

    if not context.instructions_file:
        return ""
    path = (context.project_root / context.instructions_file).resolve()
    if not path.is_relative_to(context.project_root) or not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8")[:max_chars].strip()
    except OSError:
        return ""
