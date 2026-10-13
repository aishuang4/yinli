"""托管账号、会话和管理员在线状态。"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.config.settings import Settings, assert_hosted_email_config
from src.server.auth.middleware import HostedSessionMiddleware
from src.server.auth.passwords import hash_password, verify_password
from src.server.auth.routes import admin_router, auth_router
from src.server.local_access import LocalAccessMiddleware
from src.storage import auth_records, database


@pytest.fixture()
def auth_db(tmp_path, monkeypatch):
    monkeypatch.setattr("src.config.settings.settings.auth_email_mode", "log")
    yield
    database.forget_db(database.db_path())


def test_hosted_startup_creates_default_admin(auth_db, monkeypatch):
    """托管启动时补上可登录的默认管理员，重复启动不改已有密码。"""

    monkeypatch.setattr("src.config.settings.settings.serve_mode", "hosted")
    monkeypatch.setattr("src.config.settings.settings.admin_emails", "")
    monkeypatch.setattr("src.config.settings.settings.admin_initial_password", "")
    from src.server.auth.service import DEFAULT_ADMIN_EMAIL, DEFAULT_ADMIN_PASSWORD, ensure_default_admins

    ensure_default_admins()
    client = _client(monkeypatch, "hosted")
    logged = client.post(
        "/api/auth/login",
        json={"email": DEFAULT_ADMIN_EMAIL, "password": DEFAULT_ADMIN_PASSWORD, "clientKind": "desktop"},
    )
    assert logged.status_code == 200
    assert logged.json()["user"]["role"] == "admin"
    ensure_default_admins()
    still = client.post(
        "/api/auth/login",
        json={"email": DEFAULT_ADMIN_EMAIL, "password": DEFAULT_ADMIN_PASSWORD, "clientKind": "desktop"},
    )
    assert still.status_code == 200


def test_desktop_startup_does_not_create_admin(auth_db, monkeypatch):
    monkeypatch.setattr("src.config.settings.settings.serve_mode", "desktop")
    from src.server.auth.service import ensure_default_admins
    from src.storage import control_db

    ensure_default_admins()
    assert not control_db.control_db_path().exists()


def test_desktop_mode_does_not_require_smtp():
    """桌面模式缺少 SMTP 也能通过启动检查。"""

    assert Settings(_env_file=None).serve_mode == "desktop"
    assert_hosted_email_config(Settings(_env_file=None, serve_mode="desktop", smtp_host=""))


def test_hosted_mode_without_smtp_refuses_startup():
    """托管模式在非 log 邮件配置下缺少 SMTP 时拒绝启动。"""

    config = Settings(
        _env_file=None,
        serve_mode="hosted",
        auth_email_mode="smtp",
        smtp_host="",
        smtp_from="",
    )
    with pytest.raises(Exception, match="SMTP"):
        assert_hosted_email_config(config)


def test_hosted_log_mode_can_start_without_smtp():
    assert_hosted_email_config(
        Settings(_env_file=None, serve_mode="hosted", auth_email_mode="log", smtp_host=""),
    )


def test_auth_tables_store_only_hashes(auth_db, monkeypatch):
    """控制面建库后账号表存在，令牌和验证码不以明文入库。"""

    monkeypatch.setattr("src.config.settings.settings.serve_mode", "hosted")
    from src.storage import control_db

    control_db.ensure_control_db()
    with sqlite3.connect(control_db.control_db_path()) as conn:
        names = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    assert {"auth_users", "auth_sessions", "auth_email_codes"} <= names

    token = "plain-session-token"
    code = "654321"
    auth_records.create_session(user_id="usr_hash", raw_token=token, client_kind="web")
    auth_records.save_email_code(email="a@example.com", purpose="register", raw_code=code)
    with sqlite3.connect(control_db.control_db_path()) as conn:
        session_hash = conn.execute("SELECT token_hash FROM auth_sessions").fetchone()[0]
        code_hash = conn.execute("SELECT code_hash FROM auth_email_codes").fetchone()[0]
        stored = []
        for table in ("auth_sessions", "auth_email_codes"):
            for row in conn.execute(f"SELECT * FROM {table}").fetchall():
                stored.extend(str(value) for value in row)
        blob = " ".join(stored)
    assert session_hash != token
    assert code_hash != code
    assert token not in blob
    assert code not in blob


def test_password_hash_round_trip_hides_plaintext(auth_db, monkeypatch):
    monkeypatch.setattr("src.config.settings.settings.serve_mode", "hosted")
    from src.storage import control_db

    password = "correct-horse"
    stored = hash_password(password)
    assert verify_password(password, stored) is True
    assert verify_password("wrong-horse", stored) is False
    auth_records.create_user(email="pw@example.com", password_hash=stored)
    with sqlite3.connect(control_db.control_db_path()) as conn:
        saved = conn.execute("SELECT password_hash FROM auth_users").fetchone()[0]
    assert saved != password
    assert password not in saved


def _client(monkeypatch, serve_mode: str) -> TestClient:
    monkeypatch.setattr("src.config.settings.settings.serve_mode", serve_mode)
    app = FastAPI()
    app.add_middleware(HostedSessionMiddleware)
    app.add_middleware(LocalAccessMiddleware)
    app.include_router(auth_router)
    app.include_router(admin_router)

    @app.get("/api/chat/send")
    async def chat() -> dict[str, bool]:
        return {"ok": True}

    return TestClient(app, client=("127.0.0.1", 50000), base_url="https://testserver")


def _register(client: TestClient, monkeypatch, email: str, password: str = "password1") -> list[tuple[str, str, str]]:
    sent: list[tuple[str, str, str]] = []

    def capture(target: str, purpose: str, code: str) -> None:
        sent.append((target, purpose, code))

    monkeypatch.setattr("src.server.auth.service.deliver_login_code", capture)
    response = client.post("/api/auth/register", json={"email": email, "password": password, "role": "admin"})
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    return sent


def test_register_verify_login_and_duplicate_email(auth_db, monkeypatch):
    client = _client(monkeypatch, "hosted")
    sent = _register(client, monkeypatch, "User@Example.com")
    again = _register(client, monkeypatch, "user@example.com")
    assert again == []
    from src.storage import control_db

    with sqlite3.connect(control_db.control_db_path()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM auth_users").fetchone()[0] == 1

    pending = client.post(
        "/api/auth/login",
        json={"email": "user@example.com", "password": "password1"},
    )
    assert pending.status_code == 401

    verified = client.post(
        "/api/auth/verify",
        json={"email": "user@example.com", "code": sent[0][2]},
    )
    assert verified.status_code == 200
    from src.server.auth.ownership import assign_unowned

    assign_unowned(verified.json()["user"]["id"])
    assert verified.json()["user"]["role"] == "user"
    assert verified.json()["user"]["status"] == "active"

    logged_in = client.post(
        "/api/auth/login",
        json={"email": "user@example.com", "password": "password1", "clientKind": "web"},
    )
    assert logged_in.status_code == 200
    cookie = logged_in.headers["set-cookie"]
    assert "httponly" in cookie.lower()
    assert "token" not in logged_in.json()
    assert client.get("/api/chat/send").status_code == 200

    desktop = client.post(
        "/api/auth/login",
        json={"email": "user@example.com", "password": "password1", "clientKind": "desktop"},
    )
    assert desktop.status_code == 200
    assert desktop.json()["token"]
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {desktop.json()['token']}"})
    assert me.status_code == 200


def test_reset_password_revokes_existing_session(auth_db, monkeypatch):
    client = _client(monkeypatch, "hosted")
    sent = _register(client, monkeypatch, "reset@example.com")
    client.post("/api/auth/verify", json={"email": "reset@example.com", "code": sent[0][2]})
    first = client.post(
        "/api/auth/login",
        json={"email": "reset@example.com", "password": "password1", "clientKind": "desktop"},
    )
    old_token = first.json()["token"]
    forgot = client.post("/api/auth/forgot-password", json={"email": "reset@example.com"})
    assert forgot.json() == {"ok": True}
    reset_code = sent[-1][2]
    assert sent[-1][1] == "reset"
    reset = client.post(
        "/api/auth/reset-password",
        json={"email": "reset@example.com", "code": reset_code, "password": "password2"},
    )
    assert reset.status_code == 200
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {old_token}"}).status_code == 401
    again = client.post(
        "/api/auth/login",
        json={"email": "reset@example.com", "password": "password2", "clientKind": "desktop"},
    )
    assert again.status_code == 200


def test_hosted_loopback_without_session_is_rejected(auth_db, monkeypatch):
    client = _client(monkeypatch, "hosted")
    response = client.get("/api/chat/send")
    assert response.status_code == 401
    assert response.json()["detail"] == "需要登录"


def test_sse_chunk_refreshes_last_seen(auth_db, monkeypatch):
    """流式输出会把已经过期的最近活跃时间刷新回来。"""

    import asyncio

    from src.server.auth.presence import iter_with_presence
    from src.storage import control_db

    monkeypatch.setattr("src.config.settings.settings.serve_mode", "hosted")
    auth_records.create_session(user_id="usr_sse", raw_token="sse-token", client_kind="web")
    with sqlite3.connect(control_db.control_db_path()) as conn:
        conn.execute("UPDATE auth_sessions SET last_seen_at = '2000-01-01T00:00:00+00:00'")
        conn.commit()

    async def source():
        yield b"data: ping\n\n"

    async def consume() -> None:
        async for _chunk in iter_with_presence(source(), "sse-token"):
            pass

    asyncio.run(consume())
    with sqlite3.connect(control_db.control_db_path()) as conn:
        seen = conn.execute("SELECT last_seen_at FROM auth_sessions").fetchone()[0]
    assert seen != "2000-01-01T00:00:00+00:00"


def test_desktop_loopback_chat_does_not_require_login(auth_db, monkeypatch):
    client = _client(monkeypatch, "desktop")
    response = client.get("/api/chat/send")
    assert response.status_code == 200


def test_desktop_account_and_admin_routes_are_absent(auth_db, monkeypatch):
    client = _client(monkeypatch, "desktop")
    assert client.get("/api/auth/config").status_code == 200
    assert client.get("/api/auth/desktop/session").status_code == 200
    hidden = client.post("/api/auth/login", json={"email": "a@example.com", "password": "password1"})
    assert hidden.status_code == 404
    assert hidden.json()["detail"] == "仅托管服务可用"
    assert client.get("/api/admin/users").status_code == 404


def test_auth_config_reports_serve_mode_without_secrets(auth_db, monkeypatch):
    hosted = _client(monkeypatch, "hosted")
    assert hosted.get("/api/auth/config").json() == {"serveMode": "hosted"}
    desktop = _client(monkeypatch, "desktop")
    body = desktop.get("/api/auth/config").json()
    assert body == {"serveMode": "desktop"}
    assert "smtp" not in body
    assert "password" not in body


def test_admin_can_disable_others_but_not_self(auth_db, monkeypatch):
    monkeypatch.setattr("src.config.settings.settings.admin_emails", "admin@example.com")
    monkeypatch.setattr("src.config.settings.settings.key2api_sync_token", "")
    client = _client(monkeypatch, "hosted")
    admin_sent = _register(client, monkeypatch, "admin@example.com")
    client.post("/api/auth/verify", json={"email": "admin@example.com", "code": admin_sent[0][2]})
    user_sent = _register(client, monkeypatch, "member@example.com")
    verified = client.post("/api/auth/verify", json={"email": "member@example.com", "code": user_sent[0][2]})
    member_id = verified.json()["user"]["id"]
    admin = client.post(
        "/api/auth/login",
        json={"email": "admin@example.com", "password": "password1", "clientKind": "desktop"},
    )
    member = client.post(
        "/api/auth/login",
        json={"email": "member@example.com", "password": "password1", "clientKind": "desktop"},
    )
    admin_headers = {"Authorization": f"Bearer {admin.json()['token']}"}
    member_headers = {"Authorization": f"Bearer {member.json()['token']}"}
    assert admin.json()["user"]["role"] == "admin"
    assert client.get("/api/admin/users", headers=member_headers).status_code == 403
    denied = client.post(f"/api/admin/users/{admin.json()['user']['id']}/disable", headers=admin_headers)
    assert denied.status_code == 409
    assert client.get("/api/auth/me", headers=admin_headers).status_code == 200
    disabled = client.post(f"/api/admin/users/{member_id}/disable", headers=admin_headers)
    assert disabled.status_code == 200
    assert client.get("/api/auth/me", headers=member_headers).status_code == 401
    removed = client.delete(f"/api/admin/users/{member_id}", headers=admin_headers)
    assert removed.status_code == 200
    listed = client.get("/api/admin/users", headers=admin_headers)
    assert all(item["id"] != member_id for item in listed.json()["users"])
    assert client.delete(f"/api/admin/users/{admin.json()['user']['id']}", headers=admin_headers).status_code == 409


def test_presence_aggregates_web_and_desktop_then_expires(auth_db, monkeypatch):
    monkeypatch.setattr("src.config.settings.settings.admin_emails", "admin@example.com")
    client = _client(monkeypatch, "hosted")
    sent = _register(client, monkeypatch, "admin@example.com")
    client.post("/api/auth/verify", json={"email": "admin@example.com", "code": sent[0][2]})
    web = client.post(
        "/api/auth/login",
        json={"email": "admin@example.com", "password": "password1", "clientKind": "web"},
    )
    desktop = client.post(
        "/api/auth/login",
        json={"email": "admin@example.com", "password": "password1", "clientKind": "desktop"},
    )
    headers = {"Authorization": f"Bearer {desktop.json()['token']}"}
    client.post("/api/auth/heartbeat", headers=headers)
    listing = client.get("/api/admin/users", headers=headers)
    row = listing.json()["users"][0]
    assert row["online"] is True
    assert row["clients"] == ["desktop", "web"]
    assert listing.json()["summary"]["online"] == 1

    from src.storage import control_db

    with sqlite3.connect(control_db.control_db_path()) as conn:
        conn.execute("UPDATE auth_sessions SET last_seen_at = '2000-01-01T00:00:00+00:00'")
        conn.commit()
    from src.server.auth.service import admin_directory

    stale = admin_directory()["users"][0]
    assert stale["online"] is False
    assert stale["lastSeenAt"] == "2000-01-01T00:00:00+00:00"
    assert web.status_code == 200
