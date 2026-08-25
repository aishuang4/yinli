"""通知通道抽象、应用内 inbox、设置与独立 SSE。"""

from __future__ import annotations

import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from src.notifications.dispatcher import dispatch_notification
from src.notifications.hub import notification_hub
from src.notifications.inbox import unread_count
from src.notifications.models import NotificationMessage
from src.notifications.stream import notification_event_stream
from src.scheduler.notifications import create_notification
from src.server.routes import notifications
from src.storage import database
from src.storage.database import (
    DEFAULT_PROJECT_ID,
    DEFAULT_WORKSPACE_ID,
    ensure_db,
    list_user_notifications,
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
    app.include_router(notifications.router, prefix="/api")
    with TestClient(app) as test_client:
        yield test_client


def _message(**overrides) -> NotificationMessage:
    payload = {
        "kind": "job_completed",
        "title": "周报已完成",
        "workspace_id": DEFAULT_WORKSPACE_ID,
        "project_id": DEFAULT_PROJECT_ID,
        "job_id": "job-1",
        "body": "查看产物",
    }
    payload.update(overrides)
    return NotificationMessage(**payload)


def test_main_registers_notification_stream_and_settings():
    from src.server.main import app

    paths = {getattr(route, "path", "") for route in app.routes}
    assert "/api/notifications/stream" in paths
    assert "/api/notifications/settings" in paths


def test_dispatch_persists_in_app_and_publishes(isolated_db):
    ensure_db()
    queue = notification_hub.subscribe(DEFAULT_PROJECT_ID)
    try:
        result = dispatch_notification(_message())
        assert result.in_app_record is not None
        assert result.in_app_record["kind"] == "job_completed"
        assert list_user_notifications(project_id=DEFAULT_PROJECT_ID)
        event = queue.get_nowait()
        assert event["type"] == "notification"
        assert event["notification"]["title"] == "周报已完成"
        assert event["unreadCount"] == 1
        in_app = next(item for item in result.deliveries if item.channel_id == "in_app")
        assert in_app.ok is True
    finally:
        notification_hub.unsubscribe(queue)


def test_dispatch_skips_unimplemented_external_channels(isolated_db):
    ensure_db()
    result = dispatch_notification(_message())
    skipped = {item.channel_id: item for item in result.deliveries if item.skipped}
    assert "email" not in skipped
    assert "wechat" not in skipped
    assert "feishu" not in skipped
    assert all(item.channel_id == "in_app" or item.skipped for item in result.deliveries)


def test_create_notification_adapter_writes_inbox(isolated_db):
    ensure_db()
    record = create_notification(
        kind="job_failed",
        title="周报失败",
        workspace_id=DEFAULT_WORKSPACE_ID,
        project_id=DEFAULT_PROJECT_ID,
        job_id="job-1",
        body="需要更新任务",
    )
    assert record["id"].startswith("ntf-")
    assert unread_count(project_id=DEFAULT_PROJECT_ID) == 1


def test_notification_settings_catalog_and_reject_unimplemented_enable(client):
    listed = client.get("/api/notifications/settings")
    assert listed.status_code == 200
    channels = {item["id"]: item for item in listed.json()["channels"]}
    assert channels["in_app"]["alwaysOn"] is True
    assert channels["in_app"]["implemented"] is True
    assert channels["email"]["implemented"] is False
    assert channels["wechat"]["implemented"] is False
    assert channels["feishu"]["implemented"] is False

    rejected = client.put("/api/notifications/settings", json={
        "channels": [{"id": "email", "enabled": True, "config": {"address": "a@b.com"}}],
    })
    assert rejected.status_code == 422
    assert "尚未实现" in rejected.json()["detail"]

    saved = client.put("/api/notifications/settings", json={
        "channels": [{"id": "email", "enabled": False, "config": {"address": "a@b.com"}}],
    })
    assert saved.status_code == 200
    email = next(item for item in saved.json()["channels"] if item["id"] == "email")
    assert email["enabled"] is False
    assert email["config"]["address"] == "a@b.com"


def test_unknown_channel_and_unknown_config_key_are_422(client):
    unknown = client.put("/api/notifications/settings", json={
        "channels": [{"id": "sms", "enabled": False}],
    })
    assert unknown.status_code == 422

    bad_field = client.put("/api/notifications/settings", json={
        "channels": [{"id": "email", "config": {"token": "secret"}}],
    })
    assert bad_field.status_code == 422


def test_stream_sends_snapshot_then_live_notification(isolated_db):
    ensure_db()

    async def _run() -> None:
        agen = notification_event_stream(DEFAULT_PROJECT_ID)
        try:
            first = await agen.__anext__()
            assert "event: snapshot" in first
            assert "unreadCount" in first
            dispatch_notification(_message(title="SSE 推送"))
            second = await asyncio.wait_for(agen.__anext__(), timeout=1)
            assert "event: notification" in second
            assert "SSE 推送" in second
        finally:
            await agen.aclose()

    asyncio.run(_run())


def test_mark_read_publishes_to_hub(isolated_db):
    ensure_db()
    record = create_notification(
        kind="job_completed",
        title="早餐已完成",
        workspace_id=DEFAULT_WORKSPACE_ID,
        project_id=DEFAULT_PROJECT_ID,
    )
    queue = notification_hub.subscribe(DEFAULT_PROJECT_ID)
    try:
        from src.notifications.inbox import mark_read

        while not queue.empty():
            queue.get_nowait()
        updated = mark_read(record["id"])
        assert updated["read_at"]
        event = queue.get_nowait()
        assert event["type"] == "read"
        assert event["unreadCount"] == 0
    finally:
        notification_hub.unsubscribe(queue)
