"""Workspace / Project 运行时归属解析与上下文模型。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from src.storage.database import (
    DEFAULT_PROJECT_ID,
    get_chat_session_ownership,
    get_project,
    get_workspace,
)


@dataclass(frozen=True)
class RuntimeContext:
    """一次 Runtime 不可变的 Workspace / Project 归属。"""

    workspace_id: str
    project_id: str
    project_root: Path
    session_id: str
    run_id: str
    agent_id: str
    project_name: str = ""
    instructions_file: str = ""

    def with_run_id(self, run_id: str) -> "RuntimeContext":
        """返回绑定后台 Run ID 的新上下文。"""

        return replace(self, run_id=run_id)


def resolve_runtime_context(
    *,
    project_id: str | None,
    session_id: str,
    run_id: str,
    agent_id: str,
) -> RuntimeContext:
    """解析 Project，并校验现有 Session 不会跨 Project 继续运行。"""

    session_ownership = get_chat_session_ownership(session_id) if session_id else None
    requested_project_id = str(project_id or "").strip()
    if session_ownership:
        session_project_id = str(session_ownership["projectId"])
        if requested_project_id and requested_project_id != session_project_id:
            raise ValueError(
                f"Session {session_id} 属于 Project {session_project_id}，不能在 Project {requested_project_id} 中继续。",
            )
        resolved_project_id = session_project_id
    else:
        resolved_project_id = requested_project_id or DEFAULT_PROJECT_ID

    project = get_project(resolved_project_id)
    if project is None:
        raise ValueError(f"Project 不存在: {resolved_project_id}")
    if str(project.get("status") or "") != "active":
        raise ValueError(f"Project 已归档，不能启动新运行: {resolved_project_id}")

    workspace_id = str(project.get("workspaceId") or "")
    workspace = get_workspace(workspace_id)
    if workspace is None or str(workspace.get("status") or "") != "active":
        raise ValueError(f"Workspace 不存在或已归档: {workspace_id}")
    if session_ownership and str(session_ownership["workspaceId"]) != workspace_id:
        raise ValueError(f"Session {session_id} 的 Workspace 归属与 Project 不一致。")

    project_root = Path(str(project.get("rootPath") or "")).expanduser().resolve()
    if not project_root.exists() or not project_root.is_dir():
        raise ValueError(f"Project root 不是有效目录: {project_root}")

    return RuntimeContext(
        workspace_id=workspace_id,
        project_id=resolved_project_id,
        project_root=project_root,
        session_id=session_id,
        run_id=run_id,
        agent_id=agent_id,
        project_name=str(project.get("name") or resolved_project_id),
        instructions_file=str(project.get("instructionsFile") or ""),
    )


def load_project_instructions(context: RuntimeContext, *, max_chars: int = 20_000) -> str:
    """读取 Project instructions，且拒绝越过 Project root。"""

    if not context.instructions_file:
        return ""
    path = (context.project_root / context.instructions_file).resolve()
    if not path.is_relative_to(context.project_root) or not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8")[:max_chars].strip()
    except OSError:
        return ""
