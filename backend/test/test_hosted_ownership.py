"""托管数据按账号隔离，桌面模式不套用这道过滤。"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.server.auth.middleware import HostedSessionMiddleware
from src.server.auth.routes import admin_router, auth_router
from src.server.local_access import LocalAccessMiddleware
from src.server.routes import chat, memory, settings as settings_routes, workspaces
from src.storage import database


@pytest.fixture()
def hosted_app(tmp_path, monkeypatch):
    monkeypatch.setattr("src.config.settings.settings.serve_mode", "hosted")
    monkeypatch.setattr("src.config.settings.settings.auth_email_mode", "log")
    monkeypatch.setattr("src.config.settings.settings.key2api_sync_token", "")
    monkeypatch.setattr("src.config.settings.settings.admin_emails", "admin@example.com")
    app = FastAPI()
    app.add_middleware(HostedSessionMiddleware)
    app.add_middleware(LocalAccessMiddleware)
    app.include_router(auth_router)
    app.include_router(admin_router)
    app.include_router(workspaces.router, prefix="/api")
    app.include_router(chat.router, prefix="/api/chat")
    app.include_router(memory.router, prefix="/api/memory")
    app.include_router(settings_routes.router, prefix="/api/settings")
    database.ensure_db()
    client = TestClient(app, client=("127.0.0.1", 50000), base_url="https://testserver")
    yield client
    database.forget_db(database.db_path())


def _login(client: TestClient, monkeypatch, email: str) -> tuple[str, str]:
    sent: list[tuple[str, str, str]] = []

    def capture(target: str, purpose: str, code: str) -> None:
        sent.append((target, purpose, code))

    monkeypatch.setattr("src.server.auth.service.deliver_login_code", capture)
    assert client.post("/api/auth/register", json={"email": email, "password": "password1"}).status_code == 200
    assert client.post("/api/auth/verify", json={"email": email, "code": sent[0][2]}).status_code == 200
    logged = client.post(
        "/api/auth/login",
        json={"email": email, "password": "password1", "clientKind": "desktop"},
    )
    assert logged.status_code == 200
    return logged.json()["token"], logged.json()["user"]["id"]


def test_login_sees_own_default_workspace(hosted_app, monkeypatch):
    client = hosted_app
    token, _user_id = _login(client, monkeypatch, "admin@example.com")
    visible = client.get("/api/workspaces", headers={"Authorization": f"Bearer {token}"})
    assert visible.status_code == 200
    assert any(item["id"] == "default" for item in visible.json())


def test_two_tenants_do_not_share_workspaces(hosted_app, monkeypatch):
    client = hosted_app
    admin_token, _admin_id = _login(client, monkeypatch, "admin@example.com")
    member_token, _member_id = _login(client, monkeypatch, "member@example.com")
    created = client.post(
        "/api/workspaces",
        json={"name": "甲的工作区", "description": ""},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert created.status_code == 201
    member_list = client.get(
        "/api/workspaces",
        headers={"Authorization": f"Bearer {member_token}"},
    )
    assert member_list.status_code == 200
    assert all(item["id"] != created.json()["id"] for item in member_list.json())
    assert any(item["id"] == "default" for item in member_list.json())


def test_hosted_project_stays_inside_tenant(hosted_app, monkeypatch):
    client = hosted_app
    admin_token, _admin_id = _login(client, monkeypatch, "admin@example.com")
    member_token, member_id = _login(client, monkeypatch, "member@example.com")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    member_headers = {"Authorization": f"Bearer {member_token}"}
    created = client.post("/api/workspaces", json={"name": "乙的工作区", "description": ""}, headers=member_headers)
    assert created.status_code == 201
    workspace_id = created.json()["id"]
    project = client.post(
        f"/api/workspaces/{workspace_id}/projects",
        json={"name": "乙的项目", "description": "", "rootPath": "/etc", "instructionsFile": ""},
        headers=member_headers,
    )
    assert project.status_code == 201
    project_id = project.json()["id"]
    assert f"tenants/{member_id}/projects/{project_id}" in project.json()["rootPath"]
    assert client.get(f"/api/projects/{project_id}", headers=admin_headers).status_code == 404
    assert all(item["id"] != workspace_id for item in client.get("/api/workspaces", headers=admin_headers).json())
    escaped = client.patch(
        f"/api/projects/{project_id}",
        json={"rootPath": "/etc"},
        headers=member_headers,
    )
    assert escaped.status_code == 422
    assert escaped.json()["detail"] == "Web 端不能修改 Project 目录"


def _session_payload(session_id: str, owner_user_id: str, project_id: str = "default") -> dict:
    return {
        "id": session_id,
        "title": session_id,
        "agent_id": "agent",
        "agent_name": "Agent",
        "mode": "agent",
        "workspace_id": "default",
        "project_id": project_id,
        "kind": "chat",
        "created_at": "2026-08-21T00:00:00+00:00",
        "updated_at": "2026-08-21T00:00:00+00:00",
        "message_count": 0,
        "ownerUserId": owner_user_id,
    }


def test_desktop_catalog_reads_sessions_without_owner(tmp_path, monkeypatch):
    """客户端不登录，旧库里没有归属的会话仍要出现在历史目录。"""

    monkeypatch.setattr("src.config.settings.settings.serve_mode", "desktop")
    app = FastAPI()
    app.include_router(chat.router, prefix="/api/chat")
    database.ensure_db()
    database.insert_chat_session_if_absent(_session_payload("sess-desktop", ""))
    with sqlite3.connect(database.db_path()) as conn:
        conn.execute("UPDATE chat_sessions SET owner_user_id = NULL")
        conn.commit()

    client = TestClient(app)
    response = client.get("/api/chat/sessions/catalog")
    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == ["sess-desktop"]
    database.forget_db(database.db_path())


def test_hosted_catalog_reads_only_the_signed_in_user(hosted_app, monkeypatch):
    """每个租户只看到自己库里的会话，空归属不再把整站挡成 409。"""

    from src.tenancy.context import TenantContext, use_tenant

    client = hosted_app
    admin_token, admin_id = _login(client, monkeypatch, "admin@example.com")
    member_token, member_id = _login(client, monkeypatch, "member@example.com")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    member_headers = {"Authorization": f"Bearer {member_token}"}
    with use_tenant(TenantContext.hosted(admin_id, role="admin")):
        database.insert_chat_session_if_absent(_session_payload("sess-admin", admin_id))
    with use_tenant(TenantContext.hosted(member_id)):
        database.insert_chat_session_if_absent(_session_payload("sess-member", member_id, "proj-member"))
        with sqlite3.connect(database.db_path()) as conn:
            conn.execute(
                """
                INSERT INTO chat_sessions (
                    id, title, agent_id, agent_name, mode, workspace_id, project_id, kind,
                    created_at, updated_at, message_count, model_config_id, owner_user_id
                ) VALUES (?, ?, ?, ?, 'agent', 'default', 'default', 'chat', ?, ?, 0, '', NULL)
                """,
                ("sess-legacy", "旧会话", "agent", "Agent", "2026-08-21T00:00:00+00:00", "2026-08-21T00:00:00+00:00"),
            )
            conn.commit()

    admin_catalog = client.get("/api/chat/sessions/catalog", headers=admin_headers)
    member_catalog = client.get("/api/chat/sessions/catalog", headers=member_headers)
    assert admin_catalog.status_code == 200
    assert member_catalog.status_code == 200
    assert {item["id"] for item in admin_catalog.json()} == {"sess-admin"}
    assert {item["id"] for item in member_catalog.json()} == {"sess-member", "sess-legacy"}
