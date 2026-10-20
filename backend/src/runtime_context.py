"""Workspace / Project 运行时归属解析与上下文模型。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path



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
    """過渡實現：只委託 SessionContext，忽略 Project 參數。"""

    del project_id
    from src.runtime.session_context import build_session_context
    from src.sessions.errors import SessionError

    if not session_id:
        raise SessionError("session_not_found", "缺少 Session")
    ctx = build_session_context(session_id, agent_id=agent_id, run_id=run_id)
    return RuntimeContext(
        workspace_id="",
        project_id="",
        project_root=ctx.cwd,
        session_id=ctx.session_id,
        run_id=run_id,
        agent_id=agent_id,
        project_name="",
        instructions_file="",
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
