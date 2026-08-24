"""定时任务与通知 REST、session kind 默认过滤、runs triggerSource。"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from src.scheduler.notifications import create_notification
from src.scheduler.service import ScheduledJobService
from src.scheduler.settings import is_scheduled_jobs_enabled, set_scheduled_jobs_enabled
from src.server.routes import chat, notifications, scheduled_jobs
from src.storage import database
from src.storage.database import (
    DEFAULT_PROJECT_ID,
    DEFAULT_WORKSPACE_ID,
    ensure_db,
    list_chat_runs,
    save_chat_store,
    upsert_chat_run,
)


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    """每个用例使用独立 SQLite。"""

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "agent_workstation.db")
    monkeypatch.setattr(database, "_initialized", False)
    yield
    database._initialized = False


@pytest.fixture()
def client(isolated_db):
    ensure_db()
    app = FastAPI()
    app.include_router(scheduled_jobs.router, prefix="/api")
    app.include_router(notifications.router, prefix="/api")
    app.include_router(chat.router, prefix="/api/chat")
    with TestClient(app) as test_client:
        yield test_client


def _create_payload(**overrides) -> dict:
    payload = {
        "workspaceId": DEFAULT_WORKSPACE_ID,
        "projectId": DEFAULT_PROJECT_ID,
        "name": "周五周报",
        "agentId": "agent-1",
        "scheduleKind": "weekly",
        "scheduleExpr": {"weekdays": ["fri"], "at": "18:00"},
        "timezone": "Asia/Shanghai",
        "frozenPrompt": "生成本周周报，写入 outputs/weekly-report.md",
    }
    payload.update(overrides)
    return payload


def _enable_jobs() -> None:
    set_scheduled_jobs_enabled(True)


def test_main_registers_scheduled_job_and_notification_routes():
    from src.server.main import app

    paths = {getattr(route, "path", "") for route in app.routes}
    assert "/api/scheduled-jobs" in paths
    assert "/api/scheduled-jobs/{job_id}/confirm" in paths
    assert "/api/scheduled-jobs/{job_id}/run-now" in paths
    assert "/api/notifications" in paths
    assert "/api/notifications/unread-count" in paths


def test_flag_off_write_is_403_and_list_still_readable(client):
    assert is_scheduled_jobs_enabled() is False
    created = ScheduledJobService().create_job(
        workspace_id=DEFAULT_WORKSPACE_ID,
        project_id=DEFAULT_PROJECT_ID,
        name="周五周报",
        agent_id="agent-1",
        schedule_kind="weekly",
        schedule_expr={"weekdays": ["fri"], "at": "18:00"},
        timezone="Asia/Shanghai",
        frozen_prompt="生成本周周报",
    )
    listed = client.get("/api/scheduled-jobs", params={"projectId": DEFAULT_PROJECT_ID})
    assert listed.status_code == 200
    assert any(item["id"] == created.id for item in listed.json()["jobs"])

    denied = client.post("/api/scheduled-jobs", json=_create_payload(name="另一份"))
    assert denied.status_code == 403
    assert denied.json()["detail"] == "调度未启用"
    confirm = client.post(f"/api/scheduled-jobs/{created.id}/confirm")
    assert confirm.status_code == 403
    pause = client.post(f"/api/scheduled-jobs/{created.id}/pause")
    assert pause.status_code == 403
    delete = client.delete(f"/api/scheduled-jobs/{created.id}")
    assert delete.status_code == 403


def test_confirm_activates_job(client):
    _enable_jobs()
    created = client.post("/api/scheduled-jobs", json=_create_payload())
    assert created.status_code == 201
    body = created.json()
    assert body["status"] == "pending_confirmation"
    assert body["toolScope"]["allow_bash"] is False
    assert body["toolScope"]["allow_http"] is False

    confirmed = client.post(f"/api/scheduled-jobs/{body['id']}/confirm")
    assert confirmed.status_code == 200
    payload = confirmed.json()
    assert payload["status"] == "active"
    assert payload["nextRunAt"]
    again = client.post(f"/api/scheduled-jobs/{body['id']}/confirm")
    assert again.status_code == 200
    assert again.json()["status"] == "active"


def test_confirm_auto_send_is_422(client):
    _enable_jobs()
    created = client.post(
        "/api/scheduled-jobs",
        json=_create_payload(name="自动发送", deliveryPolicy="auto_send"),
    )
    assert created.status_code == 201
    confirmed = client.post(f"/api/scheduled-jobs/{created.json()['id']}/confirm")
    assert confirmed.status_code == 422
    assert "auto_send" in confirmed.json()["detail"]


def test_run_now_conflict_when_non_terminal_firing_exists(client):
    _enable_jobs()
    created = client.post("/api/scheduled-jobs", json=_create_payload())
    job_id = created.json()["id"]
    assert client.post(f"/api/scheduled-jobs/{job_id}/confirm").status_code == 200

    first = client.post(f"/api/scheduled-jobs/{job_id}/run-now")
    assert first.status_code == 200
    firing = first.json()
    assert firing["trigger"] == "manual"
    assert firing["status"] == "queued"
    assert firing["jobId"] == job_id

    second = client.post(f"/api/scheduled-jobs/{job_id}/run-now")
    assert second.status_code == 409

    listed = client.get(f"/api/scheduled-jobs/{job_id}/runs")
    assert listed.status_code == 200
    assert len(listed.json()["runs"]) == 1


def test_session_catalog_defaults_to_chat_kind(client):
    save_chat_store({
        "sessions": [
            {
                "id": "sess-chat",
                "title": "普通对话",
                "agent_id": "agent-1",
                "agent_name": "助手",
                "mode": "agent",
                "workspace_id": DEFAULT_WORKSPACE_ID,
                "project_id": DEFAULT_PROJECT_ID,
                "kind": "chat",
                "created_at": "2026-08-21T00:00:00+00:00",
                "updated_at": "2026-08-21T00:00:01+00:00",
                "message_count": 0,
            },
            {
                "id": "sess-job",
                "title": "定时执行",
                "agent_id": "agent-1",
                "agent_name": "助手",
                "mode": "agent",
                "workspace_id": DEFAULT_WORKSPACE_ID,
                "project_id": DEFAULT_PROJECT_ID,
                "kind": "scheduled_job",
                "created_at": "2026-08-21T00:00:00+00:00",
                "updated_at": "2026-08-21T00:00:02+00:00",
                "message_count": 0,
            },
        ],
        "messages": {},
    })
    catalog = client.get("/api/chat/sessions/catalog")
    assert catalog.status_code == 200
    catalog_ids = {item["id"] for item in catalog.json()}
    assert "sess-chat" in catalog_ids
    assert "sess-job" not in catalog_ids

    sessions = client.get("/api/chat/sessions", params={"projectId": DEFAULT_PROJECT_ID})
    assert sessions.status_code == 200
    session_ids = {item["id"] for item in sessions.json()}
    assert "sess-chat" in session_ids
    assert "sess-job" not in session_ids

    all_kinds = client.get("/api/chat/sessions/catalog", params={"kind": "all"})
    assert all_kinds.status_code == 200
    all_ids = {item["id"] for item in all_kinds.json()}
    assert {"sess-chat", "sess-job"} <= all_ids


def test_list_runs_filter_by_trigger_source(client):
    upsert_chat_run({
        "id": "run-user",
        "sessionId": "sess-1",
        "agentId": "agent-1",
        "agentName": "助手",
        "task": "hello",
        "mode": "agent",
        "status": "completed",
        "triggerSource": "user",
    })
    upsert_chat_run({
        "id": "run-job",
        "sessionId": "sess-job",
        "agentId": "agent-1",
        "agentName": "助手",
        "task": "周报",
        "mode": "agent",
        "status": "completed",
        "triggerSource": "scheduled_job",
        "scheduledJobId": "job-1",
        "scheduledFiringId": "firing-1",
    })
    stored = list_chat_runs(project_id=DEFAULT_PROJECT_ID, trigger_source="scheduled_job")
    assert [item["id"] for item in stored] == ["run-job"]

    filtered = client.get(
        "/api/chat/runs",
        params={"projectId": DEFAULT_PROJECT_ID, "triggerSource": "scheduled_job"},
    )
    assert filtered.status_code == 200
    ids = {item["id"] for item in filtered.json()["runs"]}
    assert "run-job" in ids
    assert "run-user" not in ids

    unfiltered = client.get("/api/chat/runs", params={"projectId": DEFAULT_PROJECT_ID})
    assert unfiltered.status_code == 200
    all_ids = {item["id"] for item in unfiltered.json()["runs"]}
    assert {"run-job", "run-user"} <= all_ids


def test_notifications_list_read_and_unread_count(client):
    first = create_notification(
        kind="job_succeeded",
        title="周报已生成",
        workspace_id=DEFAULT_WORKSPACE_ID,
        project_id=DEFAULT_PROJECT_ID,
        job_id="job-1",
        body="查看产物",
    )
    second = create_notification(
        kind="job_failed",
        title="周报失败",
        workspace_id=DEFAULT_WORKSPACE_ID,
        project_id=DEFAULT_PROJECT_ID,
        job_id="job-1",
        body="需要更新任务",
    )
    listed = client.get(
        "/api/notifications",
        params={"projectId": DEFAULT_PROJECT_ID},
    )
    assert listed.status_code == 200
    body = listed.json()
    assert set(body) >= {"items", "unreadCount", "nextBefore"}
    assert body["unreadCount"] == 2
    assert body["nextBefore"] is None
    assert [item["id"] for item in body["items"]] == [second["id"], first["id"]]

    count = client.get(
        "/api/notifications/unread-count",
        params={"projectId": DEFAULT_PROJECT_ID},
    )
    assert count.status_code == 200
    assert count.json()["unreadCount"] == 2

    read = client.post(f"/api/notifications/{first['id']}/read")
    assert read.status_code == 200
    assert read.json()["readAt"]
    assert client.get(
        "/api/notifications/unread-count",
        params={"projectId": DEFAULT_PROJECT_ID},
    ).json()["unreadCount"] == 1

    unread_only = client.get(
        "/api/notifications",
        params={"projectId": DEFAULT_PROJECT_ID, "unreadOnly": True},
    )
    assert [item["id"] for item in unread_only.json()["items"]] == [second["id"]]

    read_all = client.post(
        "/api/notifications/read-all",
        params={"projectId": DEFAULT_PROJECT_ID},
    )
    assert read_all.status_code == 200
    assert read_all.json()["updatedCount"] == 1
    assert client.get(
        "/api/notifications/unread-count",
        params={"projectId": DEFAULT_PROJECT_ID},
    ).json()["unreadCount"] == 0


def test_delete_archives_job(client):
    _enable_jobs()
    created = client.post("/api/scheduled-jobs", json=_create_payload(name="归档我"))
    job_id = created.json()["id"]
    deleted = client.delete(f"/api/scheduled-jobs/{job_id}")
    assert deleted.status_code == 200
    assert deleted.json()["status"] == "archived"
    assert deleted.json()["archivedAt"]
    fetched = client.get(f"/api/scheduled-jobs/{job_id}")
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "archived"
