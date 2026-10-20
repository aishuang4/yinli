"""測試用 Run 快照。一律經 begin_run 凍結，不手寫快照字典。"""

from __future__ import annotations

from src.sessions.records import create_session
from src.sessions.run_binding import begin_run


def freeze_test_snapshot(session_id: str | None = None, *, agent_id: str = "default") -> dict:
    if not session_id:
        session_id = create_session(title="快照夾具", agent_id=agent_id)["id"]
    return begin_run(
        session_id,
        f"run-snap-{session_id[-8:]}",
        agent_id=agent_id,
        start_model=lambda snapshot: snapshot,
    )
