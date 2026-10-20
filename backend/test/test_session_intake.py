"""先建立 Session，Job 有 owner，Artifact 與 Run 分開驗收。"""

from fastapi.testclient import TestClient

from src.server.main import app
from src.sessions.files import record_artifact, save_session_upload
from src.sessions.intake import ensure_session_for_message
from src.sessions.jobs import assign_job_owner
from src.sessions.records import get_session
from src.storage.database import _connect, _lock, ensure_db


def _project_count() -> int:
    ensure_db()
    with _lock, _connect() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0])


def test_new_session_does_not_create_project() -> None:
    before = _project_count()
    with TestClient(app) as client:
        response = client.post("/api/sessions", json={"title": "第一則之前"})
    assert response.status_code == 201
    session_id = response.json()["id"]
    assert get_session(session_id) is not None
    assert _project_count() == before
    again = ensure_session_for_message(session_id=session_id, agent_id="default")
    assert again == session_id
    assert _project_count() == before


def test_upload_stays_in_session_root_and_artifact_is_not_accepted(tmp_path) -> None:
    session = ensure_session_for_message(session_id="sesfile1", agent_id="default")
    external = tmp_path / "external"
    external.mkdir()
    uploaded = save_session_upload(session, "note.txt", b"hello")
    assert uploaded.is_relative_to(get_session(session) and uploaded.parents[1] or uploaded)
    assert "external" not in str(uploaded)
    artifact = record_artifact(session_id=session, run_id="run-file", path=uploaded)
    assert artifact["accepted"] == "false"
    assert artifact["runId"] == "run-file"


def test_job_without_source_gets_job_owner_session() -> None:
    ensure_db()
    with _lock, _connect() as conn:
        conn.execute(
            """
            INSERT INTO scheduled_jobs (
                id, workspace_id, project_id, name, status, agent_id,
                schedule_kind, schedule_expr, timezone, frozen_prompt,
                created_at, updated_at
            ) VALUES ('job-ownerless', 'default', 'default', '日報', 'paused', 'default',
                      'daily', '0 9 * * *', 'UTC', 'prompt', 't', 't')
            """
        )
        conn.commit()
    owner = assign_job_owner("job-ownerless", "")
    session = get_session(owner)
    assert session is not None
    assert session["kind"] == "job-owner"
    with _lock, _connect() as conn:
        stored = conn.execute(
            "SELECT owner_session_id FROM scheduled_jobs WHERE id = 'job-ownerless'"
        ).fetchone()
    assert stored["owner_session_id"] == owner
    assert assign_job_owner("job-ownerless", owner) == owner
