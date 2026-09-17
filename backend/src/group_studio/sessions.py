"""进程内 Worker session 登记。waiting_permission 期间必须持有 Agent 实例。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any


@dataclass
class WorkerSession:
    """一条未终态的 Worker Run。"""

    group_id: str
    run_id: str
    member_id: str
    agent: Any
    resume: asyncio.Future[Any] | None = None
    cancelled: bool = False
    extras: dict[str, Any] = field(default_factory=dict)


_SESSIONS: dict[str, WorkerSession] = {}
_RUN_TASKS: dict[str, asyncio.Task[Any]] = {}
_INTERRUPTS: dict[str, str] = {}
_FINGERPRINTS: dict[str, tuple[Any, ...]] = {}


def session_key(group_id: str, member_id: str, run_id: str) -> str:
    return f"{group_id}:{member_id}:{run_id}"


def register_session(session: WorkerSession) -> WorkerSession:
    _SESSIONS[session_key(session.group_id, session.member_id, session.run_id)] = session
    return session


def get_session(group_id: str, member_id: str, run_id: str) -> WorkerSession | None:
    return _SESSIONS.get(session_key(group_id, member_id, run_id))


def drop_session(group_id: str, member_id: str, run_id: str) -> WorkerSession | None:
    return _SESSIONS.pop(session_key(group_id, member_id, run_id), None)


def cancel_session(group_id: str, member_id: str, run_id: str) -> None:
    session = get_session(group_id, member_id, run_id)
    if session is None:
        return
    session.cancelled = True
    if session.resume is not None and not session.resume.done():
        session.resume.set_result(None)


def cancel_sessions_for_run(group_id: str, run_id: str) -> None:
    """取消该 Run 上所有进程内 Worker session。"""

    for session in list(_SESSIONS.values()):
        if session.group_id == group_id and session.run_id == run_id:
            cancel_session(group_id, session.member_id, run_id)


def register_run_task(group_id: str, task: asyncio.Task[Any] | None) -> None:
    if task is not None:
        _RUN_TASKS[group_id] = task


def drop_run_task(group_id: str) -> None:
    _RUN_TASKS.pop(group_id, None)


def cancel_run_task(group_id: str) -> None:
    task = _RUN_TASKS.get(group_id)
    if task is not None and not task.done():
        task.cancel()


def mark_interrupt(group_id: str, source: str) -> None:
    _INTERRUPTS[group_id] = source


def take_interrupt(group_id: str) -> str | None:
    return _INTERRUPTS.pop(group_id, None)


def remember_fingerprint(run_id: str, fingerprint: tuple[Any, ...]) -> None:
    _FINGERPRINTS[run_id] = fingerprint


def take_fingerprint(run_id: str) -> tuple[Any, ...] | None:
    return _FINGERPRINTS.pop(run_id, None)


def drop_group_tool_contexts(group_id: str) -> None:
    """归档时丢掉该群的 Worker 工具上下文。"""

    from src.group_studio.tools_worker import drop_worker_tool_contexts

    drop_worker_tool_contexts(group_id)
