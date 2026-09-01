"""群聊工作室 PR12：intake 对谈、写卡不切阶段、确认接手。"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from src.group_studio.circuit import BILLABLE_STATUSES
from src.group_studio.errors import (
    GOAL_REQUIRED,
    ILLEGAL_TRANSITION,
    INTAKE_NOT_READY,
    INTAKE_WORKER_FORBIDDEN,
)
from src.group_studio.intake import IntakePhraseResult, maybe_confirm_intake
from src.group_studio.orchestrator import assert_floor_allowed
from src.group_studio.settings import set_group_studio_settings
from src.group_studio.models import GroupStudio
from src.server.routes import group_studio, settings as settings_routes
from src.storage import database
from src.storage.database import (
    DEFAULT_PROJECT_ID,
    ensure_db,
    update_group_studio_state,
)
from test.group_studio_helpers import confirm_intake_for_tests


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
    with TestClient(app) as test_client:
        yield test_client


def _create(client: TestClient, session_id: str = "sess-pr12") -> dict:
    response = client.post("/api/group-studio/from-chat", json={
        "projectId": DEFAULT_PROJECT_ID,
        "chatSessionId": session_id,
        "agentId": "default",
    })
    assert response.status_code == 200, response.text
    return response.json()


def test_drafting_allows_host_floor_forbids_worker():
    assert_floor_allowed("drafting", role="host")
    with pytest.raises(Exception) as exc:
        assert_floor_allowed("drafting", role="worker")
    assert exc.value.code == INTAKE_WORKER_FORBIDDEN


def test_drafting_not_billable():
    assert "drafting" not in BILLABLE_STATUSES


def test_from_chat_never_enters_discussing(client):
    studio = _create(client)
    assert studio["status"] == "drafting"
    assert studio["taskCard"]["goal"] == ""
    assert studio["awaitingIntakeConfirm"] is False


def test_put_goal_does_not_enter_discussing(client):
    studio = _create(client)
    client.put(f"/api/group-studio/{studio['id']}/task-card", json={
        "projectId": DEFAULT_PROJECT_ID,
        "goal": "写研报",
    })
    snapshot = client.get(f"/api/group-studio/{studio['id']}").json()
    assert snapshot["status"] == "drafting"
    assert snapshot["taskCard"]["status"] == "draft"
    types = [item["type"] for item in _events(client, studio["id"])]
    assert "GROUP_STATUS" not in types or all(
        item.get("payload", {}).get("to") != "discussing"
        for item in _events(client, studio["id"])
        if item["type"] == "GROUP_STATUS"
    )


def test_confirm_intake_without_flag_is_not_ready(client):
    studio = _create(client)
    client.put(f"/api/group-studio/{studio['id']}/task-card", json={
        "projectId": DEFAULT_PROJECT_ID,
        "goal": "写研报",
    })
    response = client.post(f"/api/group-studio/{studio['id']}/confirm-intake", json={
        "projectId": DEFAULT_PROJECT_ID,
    })
    assert response.status_code == 409
    assert response.json()["detail"] == INTAKE_NOT_READY


def test_confirm_intake_empty_goal_422(client):
    studio = _create(client)
    update_group_studio_state(studio["id"], awaiting_intake_confirm=True)
    response = client.post(f"/api/group-studio/{studio['id']}/confirm-intake", json={
        "projectId": DEFAULT_PROJECT_ID,
    })
    assert response.status_code == 422
    assert response.json()["detail"] == GOAL_REQUIRED


def test_confirm_intake_enters_discussing(client):
    studio = _create(client)
    updated = confirm_intake_for_tests(client, studio["id"], goal="写研报")
    assert updated["status"] == "discussing"
    assert updated["taskCard"]["status"] == "active"
    assert updated["awaitingIntakeConfirm"] is False
    events = _events(client, studio["id"])
    assert any(item["type"] == "GROUP_INTAKE" and item["payload"].get("reason") == "confirmed" for item in events)
    assert any(
        item["type"] == "GROUP_STATUS" and item["payload"].get("reason") == "confirm_intake"
        for item in events
    )


def test_confirm_phrase_without_flag_stays_drafting(client):
    studio = _create(client)
    client.put(f"/api/group-studio/{studio['id']}/task-card", json={
        "projectId": DEFAULT_PROJECT_ID,
        "goal": "写研报",
    })
    response = client.post(f"/api/group-studio/{studio['id']}/messages", json={
        "projectId": DEFAULT_PROJECT_ID,
        "body": "开始",
    })
    assert response.status_code == 200
    snapshot = client.get(f"/api/group-studio/{studio['id']}").json()
    assert snapshot["status"] == "drafting"


def test_confirm_phrase_with_flag_enters_discussing(client):
    studio = _create(client)
    client.put(f"/api/group-studio/{studio['id']}/task-card", json={
        "projectId": DEFAULT_PROJECT_ID,
        "goal": "写研报",
    })
    update_group_studio_state(studio["id"], awaiting_intake_confirm=True)
    response = client.post(f"/api/group-studio/{studio['id']}/messages", json={
        "projectId": DEFAULT_PROJECT_ID,
        "body": "就这样",
    })
    assert response.status_code == 200
    snapshot = client.get(f"/api/group-studio/{studio['id']}").json()
    assert snapshot["status"] == "discussing"


def test_empty_dod_can_confirm(client):
    studio = _create(client)
    updated = confirm_intake_for_tests(client, studio["id"], goal="只要目标")
    assert updated["taskCard"]["dodChecklist"] == []
    assert updated["status"] == "discussing"


def test_resume_returns_to_drafting(client):
    studio = _create(client)
    paused = client.post(f"/api/group-studio/{studio['id']}/pause", json={
        "projectId": DEFAULT_PROJECT_ID,
    })
    assert paused.status_code == 200
    resumed = client.post(f"/api/group-studio/{studio['id']}/resume", json={
        "projectId": DEFAULT_PROJECT_ID,
    })
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "drafting"


def test_maybe_confirm_intake_classifier():
    studio = GroupStudio.model_validate({
        "id": "gstu_x",
        "projectId": "default",
        "workspaceId": "default",
        "chatSessionId": "s",
        "title": "t",
        "status": "drafting",
        "currentTaskCardId": "gtask_x",
        "taskCard": {
            "id": "gtask_x",
            "groupId": "gstu_x",
            "revision": 1,
            "status": "draft",
            "goal": "目标",
            "updatedAt": "2026-08-28T00:00:00Z",
        },
        "blackboard": {
            "groupId": "gstu_x",
            "revision": 1,
            "taskCardId": "gtask_x",
            "goal": "",
            "updatedAt": "2026-08-28T00:00:00Z",
        },
        "budget": {"maxTurns": 40, "maxNoProgressTurns": 3, "usedTurns": 0, "noProgressTurns": 0},
        "awaitingRestatement": False,
        "awaitingIntakeConfirm": True,
        "createdAt": "2026-08-28T00:00:00Z",
        "updatedAt": "2026-08-28T00:00:00Z",
    })
    assert maybe_confirm_intake(studio, "开始") is IntakePhraseResult.confirmed
    assert maybe_confirm_intake(studio, "还没开始调研") is IntakePhraseResult.not_a_phrase
    studio.awaiting_intake_confirm = False
    assert maybe_confirm_intake(studio, "开始") is IntakePhraseResult.blocked_no_flag


def test_confirm_intake_not_on_discussing(client):
    studio = _create(client)
    confirm_intake_for_tests(client, studio["id"], goal="已开会")
    again = client.post(f"/api/group-studio/{studio['id']}/confirm-intake", json={
        "projectId": DEFAULT_PROJECT_ID,
    })
    assert again.status_code == 409
    assert again.json()["detail"] == ILLEGAL_TRANSITION


def _events(client: TestClient, group_id: str) -> list[dict]:
    from src.storage.database import list_group_studio_events_after

    return list_group_studio_events_after(group_id, 0)
