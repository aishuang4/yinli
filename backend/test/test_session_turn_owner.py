"""Chat 單輪的訊息、Run、Trace、Artifact 使用同一個 SessionContext。"""

from src.sessions.files import record_artifact, save_session_upload
from src.sessions.intake import ensure_session_for_message
from src.server.routes.chat.run_context import require_session_context
from src.runtime_context import resolve_runtime_context
from src.storage.database import _connect, _lock, ensure_db


def test_turn_owners_share_one_session() -> None:
    ensure_db()
    with _lock, _connect() as conn:
        before = int(conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0])
    session_id = ensure_session_for_message(session_id="sesown1", agent_id="default")
    runtime = resolve_runtime_context(
        project_id="default",
        session_id=session_id,
        run_id="run-own",
        agent_id="default",
    )
    owner = require_session_context(runtime, "default")
    uploaded = save_session_upload(session_id, "result.txt", b"ok")
    artifact = record_artifact(session_id=owner.session_id, run_id=owner.run_id, path=uploaded)
    assert owner.session_id == session_id
    assert artifact["sessionId"] == session_id
    assert artifact["runId"] == "run-own"
    with _lock, _connect() as conn:
        after = int(conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0])
    assert after == before
