"""租户创建与会话中间件。沙箱清理留到任务组 6。"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.server.auth.middleware import HostedSessionMiddleware
from src.storage import auth_records, control_records
from src.tenancy.context import current_tenant
from src.tenancy.lifecycle import ensure_tenant

pytest_plugins = ["test.hosted_support"]

HOSTED_USER = {
    "id": "usr_" + "c" * 32,
    "email": "tenant@example.com",
    "role": "user",
    "status": "active",
}


def test_ensure_tenant_is_idempotent_and_seeds_default_project(hosted_env, monkeypatch):
    from src.catalog.loader import get_registered_agent
    from src.config.model_catalog import models_path
    from src.config.runtime_paths import projects_root, skill_packages_root
    from src.scheduler.settings import get_scheduled_jobs_settings_public
    from src.storage import database
    from src.tenancy.context import use_tenant

    monkeypatch.setattr("src.config.settings.settings.key2api_sync_token", "")
    first = ensure_tenant(HOSTED_USER)
    control_records.set_tenant_status(HOSTED_USER["id"], "disabled")
    second = ensure_tenant(HOSTED_USER)
    assert second.tenant_id == first.tenant_id
    assert control_records.get_tenant(HOSTED_USER["id"])["status"] == "disabled"
    with use_tenant(first):
        project = database.get_project("default")
        assert project is not None
        assert str(project["rootPath"]).startswith(str(projects_root()))
        assert skill_packages_root().is_dir()
        assert models_path().is_file()
        settings = get_scheduled_jobs_settings_public()
        assert settings["enabled"] is True
        assert get_registered_agent("default") is not None


def test_middleware_binds_active_tenant_and_rejects_disabled(hosted_env):
    app = FastAPI()
    app.add_middleware(HostedSessionMiddleware)

    @app.get("/api/who")
    async def who() -> dict[str, str]:
        return {"tenantId": current_tenant().tenant_id}

    @app.get("/api/auth/config")
    async def config() -> dict[str, str]:
        return {"ok": "1"}

    client = TestClient(app, client=("127.0.0.1", 50000), base_url="https://testserver")
    assert client.get("/api/auth/config").status_code == 200
    assert client.get("/api/who").status_code == 401

    auth_records.create_user(email="gate@example.com", password_hash="hash")
    user = auth_records.get_user_by_email("gate@example.com")
    auth_records.update_user(user["id"], status="active", verified_at="2026-01-01T00:00:00+00:00")
    user = auth_records.get_user(user["id"])
    session = auth_records.create_session(user_id=user["id"], raw_token="gate-token", client_kind="web")
    assert session["id"]
    control_records.insert_tenant(user["id"])
    headers = {"Authorization": "Bearer gate-token"}
    seen = client.get("/api/who", headers=headers)
    assert seen.status_code == 200
    assert seen.json()["tenantId"] == user["id"]

    control_records.set_tenant_status(user["id"], "disabled")
    blocked = client.get("/api/who", headers=headers)
    assert blocked.status_code == 403
    assert blocked.json()["detail"] == "账号已停用"


def test_disable_revokes_sessions_and_delete_removes_directory(hosted_env, monkeypatch):
    import asyncio

    from src.server.routes.chat.background_runs import ChatBackgroundRun, _runs
    from src.tenancy.lifecycle import delete_tenant, disable_tenant, tenant_directory

    monkeypatch.setattr("src.config.settings.settings.key2api_sync_token", "")
    auth_records.create_user(email="gone@example.com", password_hash="hash")
    user = auth_records.get_user_by_email("gone@example.com")
    auth_records.update_user(user["id"], status="active", verified_at="2026-01-01T00:00:00+00:00")
    user = auth_records.get_user(user["id"])
    ensure_tenant(user)
    auth_records.create_session(user_id=user["id"], raw_token="life-token", client_kind="web")
    root = tenant_directory(user["id"])
    assert root.is_dir()

    async def scenario() -> None:
        task = asyncio.create_task(asyncio.sleep(30))
        run = ChatBackgroundRun(id="run-life", session_id="s", tenant_id=user["id"])
        run.async_task = task
        _runs[run.id] = run
        try:
            disable_tenant(user["id"])
            assert task.cancelled() or task.cancelling()
            assert control_records.get_tenant(user["id"])["status"] == "disabled"
            assert auth_records.get_session_by_token("life-token") is None
        finally:
            _runs.pop(run.id, None)
            if not task.done():
                task.cancel()

    asyncio.run(scenario())
    delete_tenant(user["id"])
    assert auth_records.get_user(user["id"]) is None
    assert not root.exists()
    assert control_records.get_tenant(user["id"])["status"] == "deleted"
