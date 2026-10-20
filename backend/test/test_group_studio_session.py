"""群聊歸屬切到 owner Session：跨 Session 拒絕、檔案、波次凍結、歸檔。"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from src.config.runtime_paths import session_group_studio_root
from src.group_studio.settings import set_group_studio_settings
from src.group_studio.wave_grant import capture_wave_grant, load_wave_grant, store_wave_grant
from src.server.routes import group_studio, settings as settings_routes
from src.sessions.errors import SessionError
from src.sessions.service import archive_session, hard_delete_session
from src.storage import database
from src.storage.database import DEFAULT_PROJECT_ID, ensure_db, insert_group_studio_run


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.config.product_features.is_surface_enabled",
        lambda key: True,
    )
    yield
    database.forget_db(database.db_path())


@pytest.fixture()
def client(isolated_db):
    ensure_db()
    set_group_studio_settings(enabled=True)
    app = FastAPI()
    app.include_router(group_studio.router, prefix="/api/group-studio")
    app.include_router(settings_routes.router, prefix="/api/settings")
    with TestClient(app) as test_client:
        yield test_client


def _create(client: TestClient, session_id: str) -> dict:
    response = client.post("/api/group-studio/from-chat", json={
        "projectId": DEFAULT_PROJECT_ID,
        "chatSessionId": session_id,
        "agentId": "default",
        "draftText": "只要房间",
    })
    assert response.status_code == 200, response.text
    return response.json()


def test_old_payload_still_opens_and_other_session_is_rejected(client):
    body = _create(client, "sess-owner")
    same = client.get(f"/api/group-studio/{body['id']}")
    assert same.status_code == 200
    owned = client.get(
        f"/api/group-studio/{body['id']}",
        params={"sessionId": "sess-owner"},
    )
    assert owned.status_code == 200
    foreign = client.get(
        f"/api/group-studio/{body['id']}",
        params={"sessionId": "sess-other"},
    )
    assert foreign.status_code == 422
    assert foreign.json()["detail"] == "cross_session"
    listed = client.get("/api/group-studio", params={
        "projectId": DEFAULT_PROJECT_ID,
        "sessionId": "sess-other",
    })
    assert listed.status_code == 200
    assert listed.json()["studios"] == []


def test_studio_files_live_under_owner_session(client, tmp_path):
    body = _create(client, "sess-files")
    root = session_group_studio_root("sess-files", body["id"])
    assert (root / "inputs").is_dir()
    assert (root / "workspace").is_dir()
    assert ".agent-workstation" not in root.parts
    external = tmp_path / "outside-work"
    external.mkdir()
    assert not (external / ".agent-workstation").exists()


def test_wave_grant_changes_between_waves_and_stays_inside_a_wave(client, tmp_path):
    from src.group_studio.worker_runtime import build_worker_runtime_spec

    body = _create(client, "sess-wave")
    member = {
        "id": "gmbr_wave",
        "groupId": body["id"],
        "role": "worker",
        "agentId": "default",
        "displayName": "默认助手",
        "status": "active",
        "muted": False,
    }
    first = capture_wave_grant("sess-wave", model_config_id="model-a")
    insert_group_studio_run({
        "id": "grun_wave",
        "groupId": body["id"],
        "kind": "discussion",
        "status": "running",
        "speakerMemberId": member["id"],
    })
    store_wave_grant("grun_wave", first)
    from src.sessions.records import get_run_snapshot
    from src.sessions.run_binding import begin_run

    first_snap = begin_run(
        "sess-wave",
        "run-wave-a",
        agent_id="default",
        permission_mode="bypass",
        start_model=lambda item: item,
    )
    writable = tmp_path / "grant-root"
    writable.mkdir()
    with database._lock, database._connect() as conn:
        conn.execute(
            """
            INSERT INTO session_allowed_roots (
                session_id, normalized_path, access_mode, source, created_at
            ) VALUES (?, ?, 'read_write', 'user', '2026-01-01T00:00:00Z')
            """,
            ("sess-wave", str(writable)),
        )
        conn.commit()
    frozen = load_wave_grant("grun_wave")
    assert frozen is not None
    assert frozen["roots"] == []
    assert frozen["modelConfigId"] == "model-a"
    nxt = capture_wave_grant("sess-wave", model_config_id="model-b")
    assert nxt["roots"]
    assert nxt["modelConfigId"] == "model-b"
    second_snap = begin_run(
        "sess-wave",
        "run-wave-b",
        agent_id="default",
        permission_mode="bypass",
        start_model=lambda item: item,
    )
    held = build_worker_runtime_spec(body, member, frozen, run_snapshot=first_snap)
    moved = build_worker_runtime_spec(body, member, nxt, run_snapshot=second_snap)
    assert get_run_snapshot("run-wave-a") is not None
    assert get_run_snapshot("run-wave-b") is not None
    assert str(writable.resolve()) not in held[0].extra_filesystem_scopes
    assert str(writable.resolve()) in moved[0].extra_filesystem_scopes
    assert "group-studio" in held[0].guardrails.filesystem_scope


def test_speaking_delete_fails_and_idle_room_can_be_archived(client):
    speaking = _create(client, "sess-speaking")
    insert_group_studio_run({
        "id": "grun_speak",
        "groupId": speaking["id"],
        "kind": "discussion",
        "status": "running",
        "speakerMemberId": "gmbr_x",
    })
    with pytest.raises(SessionError) as deleted:
        hard_delete_session("sess-speaking")
    assert deleted.value.code == "session_has_active_studios"
    with pytest.raises(SessionError) as archived:
        archive_session("sess-speaking")
    assert archived.value.code == "session_has_active_studios"

    _create(client, "sess-idle")
    archived_session = archive_session("sess-idle")
    assert archived_session["status"] == "archived"
