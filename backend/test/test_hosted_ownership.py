"""托管数据按账号隔离，桌面模式不套用这道过滤。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.memory.markdown_store import MarkdownMemoryStore
from src.server.auth.middleware import HostedSessionMiddleware
from src.runtime_context import resolve_runtime_context
from src.server.auth.ownership import assign_unowned, current_owner_id, path_is_inside_user, reset_owner, set_owner
from src.server.auth.routes import admin_router, auth_router
from src.server.local_access import LocalAccessMiddleware
from src.server.routes import chat, memory, settings as settings_routes, workspaces
from src.server.routes.chat.store import _append_user_message
from src.storage import database


@pytest.fixture()
def hosted_app(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "owned.db")
    monkeypatch.setattr(database, "_initialized", False)
    monkeypatch.setattr("src.config.settings.settings.serve_mode", "hosted")
    monkeypatch.setattr("src.config.settings.settings.auth_email_mode", "log")
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
    database._initialized = False


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


def test_unassigned_rows_block_until_command(hosted_app, monkeypatch):
    client = hosted_app
    token, user_id = _login(client, monkeypatch, "admin@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    blocked = client.get("/api/workspaces", headers=headers)
    assert blocked.status_code == 409
    assigned = client.post("/api/admin/ownership/assign", json={"userId": user_id}, headers=headers)
    assert assigned.status_code == 200
    assert assigned.json()["updated"] >= 1
    visible = client.get("/api/workspaces", headers=headers)
    assert visible.status_code == 200
    assert any(item["id"] == "default" for item in visible.json())


def test_new_user_does_not_use_the_shared_default_project(hosted_app, monkeypatch):
    client = hosted_app
    admin_token, admin_id = _login(client, monkeypatch, "admin@example.com")
    member_token, member_id = _login(client, monkeypatch, "member@example.com")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    member_headers = {"Authorization": f"Bearer {member_token}"}
    assert client.post("/api/admin/ownership/assign", json={"userId": admin_id}, headers=admin_headers).status_code == 200

    listed = client.get("/api/workspaces", headers=member_headers)
    assert listed.status_code == 200
    assert listed.json()
    assert all(item["id"] != "default" for item in listed.json())
    projects = client.get(f"/api/workspaces/{listed.json()[0]['id']}/projects", headers=member_headers)
    assert projects.status_code == 200
    assert len(projects.json()) == 1
    owner = set_owner(member_id)
    try:
        context = resolve_runtime_context(
            project_id="default",
            session_id="new-session",
            run_id="",
            agent_id="default",
        )
    finally:
        reset_owner(owner)
    assert context.project_id == projects.json()[0]["id"]
    assert f"users/{member_id}/projects/" in str(context.project_root)


def test_two_users_cannot_read_each_other(hosted_app, monkeypatch, tmp_path):
    client = hosted_app
    admin_token, admin_id = _login(client, monkeypatch, "admin@example.com")
    member_token, member_id = _login(client, monkeypatch, "member@example.com")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    member_headers = {"Authorization": f"Bearer {member_token}"}
    client.post("/api/admin/ownership/assign", json={"userId": admin_id}, headers=admin_headers)

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
    assert f"users/{member_id}/projects/{project_id}" in project.json()["rootPath"]
    assert client.get(f"/api/projects/{project_id}", headers=admin_headers).status_code == 404
    assert client.get("/api/workspaces", headers=admin_headers).json()
    assert all(item["id"] != workspace_id for item in client.get("/api/workspaces", headers=admin_headers).json())

    escaped = client.patch(
        f"/api/projects/{project_id}",
        json={"rootPath": "/etc"},
        headers=member_headers,
    )
    assert escaped.status_code == 422
    parent = str(Path(project.json()["rootPath"]).resolve().parent.parent)
    dotted = client.patch(
        f"/api/projects/{project_id}",
        json={"rootPath": parent},
        headers=member_headers,
    )
    assert dotted.status_code == 422

    outside = tmp_path / "outside"
    outside.mkdir()
    link = Path(project.json()["rootPath"]) / "link"
    link.symlink_to(outside, target_is_directory=True)
    linked = client.patch(
        f"/api/projects/{project_id}",
        json={"rootPath": str(link)},
        headers=member_headers,
    )
    assert linked.status_code == 422
    assert path_is_inside_user(member_id, link) is False

    owner = set_owner(member_id)
    try:
        assert current_owner_id() == member_id
        _append_user_message(
            session_id="sess-member",
            agent_id="agent",
            agent_name="Agent",
            mode="agent",
            model_name="test",
            content="只有乙能看见",
            images=[],
            workspace_id=workspace_id,
            project_id=project_id,
        )
    finally:
        from src.server.auth.ownership import reset_owner
        reset_owner(owner)
    hidden = client.get("/api/chat/sessions", headers=admin_headers, params={"projectId": project_id})
    assert hidden.status_code == 200
    assert hidden.json() == []
    seen = client.get("/api/chat/sessions", headers=member_headers, params={"projectId": project_id})
    assert [item["id"] for item in seen.json()] == ["sess-member"]
    missing = client.get(
        "/api/chat/sessions/sess-member/messages",
        headers=admin_headers,
        params={"projectId": project_id},
    )
    assert missing.status_code == 404

    client.put(
        "/api/memory/habits",
        headers=member_headers,
        params={"userId": member_id},
        json={"content": "乙的习惯"},
    )
    denied = client.get("/api/memory/habits", headers=admin_headers, params={"userId": member_id})
    assert denied.status_code == 404
    own = client.get("/api/memory/habits", headers=member_headers)
    assert "乙的习惯" in own.json()["content"]
    assert MarkdownMemoryStore().read_habits(admin_id).content != MarkdownMemoryStore().read_habits(member_id).content

    catalog = client.get("/api/settings/models", headers=member_headers)
    assert catalog.status_code == 200
    assert "test-only" not in catalog.text
    assert "apiKey" not in catalog.text or "apiKeyConfigured" in catalog.text
    forbidden = client.put("/api/settings/models", headers=member_headers, json=catalog.json())
    assert forbidden.status_code == 403
    saved = client.put("/api/settings/models", headers=admin_headers, json=catalog.json())
    assert saved.status_code == 200

    with sqlite3.connect(database.DB_PATH) as conn:
        owners = {
            row[0]
            for row in conn.execute(
                "SELECT owner_user_id FROM projects WHERE id = ?",
                (project_id,),
            )
        }
    assert owners == {member_id}
    with pytest.raises(ValueError):
        assign_unowned("missing")


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

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "desktop.db")
    monkeypatch.setattr(database, "_initialized", False)
    monkeypatch.setattr("src.config.settings.settings.serve_mode", "desktop")
    app = FastAPI()
    app.include_router(chat.router, prefix="/api/chat")
    database.ensure_db()
    database.save_chat_store({
        "sessions": [_session_payload("sess-desktop", "")],
        "messages": {},
    })
    with sqlite3.connect(database.DB_PATH) as conn:
        conn.execute("UPDATE chat_sessions SET owner_user_id = NULL")
        conn.commit()

    client = TestClient(app)
    response = client.get("/api/chat/sessions/catalog")
    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == ["sess-desktop"]
    database._initialized = False


def test_hosted_catalog_reads_only_the_signed_in_user(hosted_app, monkeypatch):
    """Web 只返回当前用户的会话；一条空归属不会把其他人的历史读成空。"""

    from src.server.routes.chat.store import _load_store

    client = hosted_app
    admin_token, admin_id = _login(client, monkeypatch, "admin@example.com")
    member_token, member_id = _login(client, monkeypatch, "member@example.com")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    member_headers = {"Authorization": f"Bearer {member_token}"}
    assigned = client.post("/api/admin/ownership/assign", json={"userId": admin_id}, headers=admin_headers)
    assert assigned.status_code == 200

    database.save_chat_store({
        "sessions": [
            _session_payload("sess-admin", admin_id),
            _session_payload("sess-member", member_id, "proj-member"),
        ],
        "messages": {},
    })
    admin_catalog = client.get("/api/chat/sessions/catalog", headers=admin_headers)
    member_catalog = client.get("/api/chat/sessions/catalog", headers=member_headers)
    assert admin_catalog.status_code == 200
    assert member_catalog.status_code == 200
    assert [item["id"] for item in admin_catalog.json()] == ["sess-admin"]
    assert [item["id"] for item in member_catalog.json()] == ["sess-member"]

    with sqlite3.connect(database.DB_PATH) as conn:
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

    blocked = client.get("/api/chat/sessions/catalog", headers=member_headers)
    assert blocked.status_code == 409
    assert {session.id for session in _load_store().sessions} == {"sess-admin", "sess-member", "sess-legacy"}

    claimed = client.post("/api/admin/ownership/assign", json={"userId": admin_id}, headers=admin_headers)
    assert claimed.status_code == 200
    admin_after = client.get("/api/chat/sessions/catalog", headers=admin_headers)
    member_after = client.get("/api/chat/sessions/catalog", headers=member_headers)
    assert {item["id"] for item in admin_after.json()} == {"sess-admin", "sess-legacy"}
    assert [item["id"] for item in member_after.json()] == ["sess-member"]
