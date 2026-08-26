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
