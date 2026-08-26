"""群聊工作室 PR 9：DoD 结项、归档后再开、catalog 徽章、启动对账。"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from src.group_studio.errors import DOD_INCOMPLETE, ILLEGAL_TRANSITION
from src.group_studio.lifecycle import archive_for_chat_session_delete, reconcile_interrupted_studios
from src.group_studio.settings import set_group_studio_settings
from src.server.routes import group_studio, settings as settings_routes
from src.server.routes.chat.handlers import sessions as chat_sessions
from src.server.routes.chat.router import router as chat_router
from src.storage import database
from src.storage.database import (
    DEFAULT_PROJECT_ID,
    ensure_db,
    get_active_group_studio_by_chat_session,
    get_group_studio_snapshot,
    insert_group_studio_run,
    list_group_studio_events_after,
    set_group_studio_current_run_id,
)


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "agent_workstation.db")
    monkeypatch.setattr(database, "_initialized", False)
    yield
    database._initialized = False


@pytest.fixture()
def client(isolated_db):
    ensure_db()
    set_group_studio_settings(enabled=True)
    app = FastAPI()
    app.include_router(group_studio.router, prefix="/api/group-studio")
    app.include_router(settings_routes.router, prefix="/api/settings")
    app.include_router(chat_router, prefix="/api/chat")
    with TestClient(app) as test_client:
        yield test_client


def _create_discussing(client: TestClient, session_id: str = "sess-pr9") -> dict:
    created = client.post("/api/group-studio/from-chat", json={
        "projectId": DEFAULT_PROJECT_ID,
        "chatSessionId": session_id,
        "agentId": "default",
        "draftText": "完成竞品分析",
    })
    assert created.status_code == 200, created.text
    return created.json()


def _enter_reviewing(client: TestClient, group_id: str) -> None:
    executing = client.post(f"/api/group-studio/{group_id}/start-execution", json={
        "projectId": DEFAULT_PROJECT_ID,
    })
    assert executing.status_code == 200, executing.text
    reviewing = client.post(f"/api/group-studio/{group_id}/start-review", json={
        "projectId": DEFAULT_PROJECT_ID,
    })
    assert reviewing.status_code == 200, reviewing.text
    assert reviewing.json()["status"] == "reviewing"


def test_close_requires_review_then_complete_dod(client):
    studio = _create_discussing(client, "sess-close")
    group_id = studio["id"]
    too_early = client.post(f"/api/group-studio/{group_id}/close", json={
        "projectId": DEFAULT_PROJECT_ID,
        "confirm": False,
    })
    assert too_early.status_code == 409
    assert too_early.json()["detail"] == ILLEGAL_TRANSITION

    _enter_reviewing(client, group_id)
    closing = client.post(f"/api/group-studio/{group_id}/close", json={
        "projectId": DEFAULT_PROJECT_ID,
        "confirm": False,
    })
    assert closing.status_code == 200, closing.text
    assert closing.json()["status"] == "closing"
    assert closing.json()["taskCard"]["status"] == "frozen_for_close"

    empty = client.post(f"/api/group-studio/{group_id}/close", json={
        "projectId": DEFAULT_PROJECT_ID,
        "confirm": True,
    })
    assert empty.status_code == 409
    assert empty.json()["detail"] == DOD_INCOMPLETE

    added = client.put(f"/api/group-studio/{group_id}/task-card", json={
        "projectId": DEFAULT_PROJECT_ID,
        "dodChecklist": [{"id": "gdod_1", "text": "交付分析文档", "checked": False}],
    })
    assert added.status_code == 200
    still = client.post(f"/api/group-studio/{group_id}/close", json={
        "projectId": DEFAULT_PROJECT_ID,
        "confirm": True,
    })
    assert still.status_code == 409
    assert still.json()["detail"] == DOD_INCOMPLETE

    checked = client.put(f"/api/group-studio/{group_id}/task-card", json={
        "projectId": DEFAULT_PROJECT_ID,
        "dodChecklist": [{"id": "gdod_1", "text": "交付分析文档", "checked": True}],
    })
    assert checked.status_code == 200
    events = list_group_studio_events_after(group_id, 0)
    assert any(item["type"] == "GROUP_DOD" for item in events)

    archived = client.post(f"/api/group-studio/{group_id}/close", json={
        "projectId": DEFAULT_PROJECT_ID,
        "confirm": True,
    })
    assert archived.status_code == 200, archived.text
    assert archived.json()["status"] == "archived"
    assert get_active_group_studio_by_chat_session("sess-close") is None

    again = client.post("/api/group-studio/from-chat", json={
        "projectId": DEFAULT_PROJECT_ID,
        "chatSessionId": "sess-close",
        "agentId": "default",
        "draftText": "下一份分析",
    })
    assert again.status_code == 200
    assert again.json()["id"] != group_id
    assert again.json()["status"] == "discussing"


def test_catalog_attaches_group_studio_hint(client):
    studio = _create_discussing(client, "sess-hint")
    catalog = client.get("/api/chat/sessions/catalog")
    assert catalog.status_code == 200
    match = next((item for item in catalog.json() if item["id"] == "sess-hint"), None)
    assert match is not None
    hint = match.get("groupStudio") or match.get("group_studio")
    assert hint["id"] == studio["id"]
    assert hint["status"] == "discussing"


def test_delete_session_archives_studio(client):
    studio = _create_discussing(client, "sess-del")
    archive_for_chat_session_delete("sess-del")
    assert get_active_group_studio_by_chat_session("sess-del") is None
    snapshot = get_group_studio_snapshot(studio["id"])
    assert snapshot is not None
    assert snapshot["status"] == "archived"


def test_startup_reconcile_pauses_open_run(client):
    studio = _create_discussing(client, "sess-rec")
    insert_group_studio_run({
        "id": "grun_open",
        "groupId": studio["id"],
        "kind": "discussion",
        "status": "running",
        "speakerMemberId": studio["members"][0]["id"],
    })
    set_group_studio_current_run_id(studio["id"], "grun_open")
    count = reconcile_interrupted_studios()
    assert count == 1
    snapshot = get_group_studio_snapshot(studio["id"])
    assert snapshot is not None
    assert snapshot["status"] == "paused"
    assert snapshot["currentRunId"] is None
    assert snapshot["phaseBeforePause"] == "discussing"


def test_chat_handlers_do_not_import_group_studio_package():
    from pathlib import Path

    text = Path(chat_sessions.__file__).read_text(encoding="utf-8")
    assert "src.group_studio" not in text
    assert "routes.group_studio" not in text
