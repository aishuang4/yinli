"""云端转发：鉴权、错误映射、审计，以及不记录正文。"""

from __future__ import annotations

import json
import logging
import sqlite3

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.server.auth.middleware import HostedSessionMiddleware
from src.server.auth.routes import admin_router, auth_router
from src.server.local_access import LocalAccessMiddleware
from src.server.platform_gateway import router as gateway_router
from src.storage import auth_records, control_db, database
from src.storage.control_records import set_tenant_status


class _FakeClient:
    def __init__(self, *args, **kwargs) -> None:
        self.payloads: list[dict] = _FakeClient.payloads

    payloads: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def aclose(self) -> None:
        return None

    async def post(self, url, headers=None, json=None):
        self.payloads.append(json)
        if json["messages"][0]["content"] == "SECRET_PROMPT_TEXT":
            return httpx.Response(401, json={"error": {"message": "bad key"}})
        return httpx.Response(200, json={
            "id": "ok",
            "choices": [{"message": {"content": "reply"}}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 3},
        })

    def build_request(self, method, url, headers=None, json=None):
        self.payloads.append(json)
        return json

    async def send(self, request, stream=False):
        body = b'data: {"choices":[],"usage":{"prompt_tokens":5,"completion_tokens":6}}\n\n'

        class _Response:
            status_code = 200

            async def aiter_bytes(self):
                yield body

            async def aread(self):
                return body

            async def aclose(self):
                return None

        return _Response()


@pytest.fixture()
def gateway(monkeypatch):
    monkeypatch.setattr("src.config.settings.settings.serve_mode", "hosted")
    monkeypatch.setattr("src.config.settings.settings.auth_email_mode", "log")
    monkeypatch.setattr("src.config.settings.settings.key2api_sync_token", "svc-token")
    monkeypatch.setattr("src.server.auth.key2api.ensure_provider_key", lambda user: None)
    _FakeClient.payloads = []
    monkeypatch.setattr("src.server.platform_gateway.httpx.AsyncClient", _FakeClient)
    monkeypatch.setattr(
        "src.server.platform_gateway.list_platform_models",
        lambda user_id: [{"id": "platform:demo"}],
    )
    app = FastAPI()
    app.add_middleware(HostedSessionMiddleware)
    app.add_middleware(LocalAccessMiddleware)
    app.include_router(auth_router)
    app.include_router(admin_router)
    app.include_router(gateway_router)
    database.ensure_db()
    client = TestClient(app, client=("127.0.0.1", 50000), base_url="https://testserver")
    yield client
    database.forget_db(database.db_path())
    control_db.forget_control_db()


def _login(client: TestClient, monkeypatch, email: str = "user@example.com") -> str:
    sent: list[str] = []
    monkeypatch.setattr(
        "src.server.auth.service.deliver_login_code",
        lambda _email, _purpose, code: sent.append(code),
    )
    client.post("/api/auth/register", json={"email": email, "password": "password1"})
    client.post("/api/auth/verify", json={"email": email, "code": sent[0]})
    logged = client.post(
        "/api/auth/login",
        json={"email": email, "password": "password1", "clientKind": "desktop"},
    )
    user = auth_records.get_user_by_email(email)
    auth_records.save_provider_key(
        user_id=user["id"],
        token="k2a_secret",
        key2api_user_id="k",
        key2api_token_id="t",
        status="active",
    )
    return logged.json()["token"]


def _audit_rows() -> list[tuple]:
    with sqlite3.connect(control_db.control_db_path()) as conn:
        return conn.execute(
            "SELECT model_config_id, prompt_tokens, completion_tokens, status, client_kind FROM platform_call_audit"
        ).fetchall()


def test_completion_audits_usage_without_body(gateway, monkeypatch, caplog):
    token = _login(gateway, monkeypatch)
    prompt = "please lookup the record"
    with caplog.at_level(logging.DEBUG, logger="src.server.platform_gateway"):
        response = gateway.post(
            "/api/platform/v1/chat/completions",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "model": "platform:demo",
                "messages": [{"role": "user", "content": prompt}],
                "tools": [{"type": "function", "function": {"name": "lookup"}}],
            },
        )
    assert response.status_code == 200
    assert response.json()["usage"]["prompt_tokens"] == 2
    assert prompt not in response.text
    assert prompt not in caplog.text
    assert _FakeClient.payloads[0]["model"] == "demo"
    assert _FakeClient.payloads[0]["tools"][0]["function"]["name"] == "lookup"
    assert _audit_rows() == [("platform:demo", 2, 3, 200, "desktop")]


def test_upstream_key_rejection_is_gateway_error(gateway, monkeypatch):
    token = _login(gateway, monkeypatch)
    response = gateway.post(
        "/api/platform/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={"model": "platform:demo", "messages": [{"role": "user", "content": "SECRET_PROMPT_TEXT"}]},
    )
    assert response.status_code == 502
    assert response.json()["detail"] == "平台 Key 不可用"
    assert "SECRET_PROMPT_TEXT" not in response.text
    assert _audit_rows()[0][3] == 502


def test_unknown_model_is_404(gateway, monkeypatch):
    token = _login(gateway, monkeypatch)
    response = gateway.post(
        "/api/platform/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={"model": "platform:missing", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 404
    assert _FakeClient.payloads == []
    assert _audit_rows()[0][3] == 404


def test_stream_passes_chunks_and_usage(gateway, monkeypatch):
    token = _login(gateway, monkeypatch)
    response = gateway.post(
        "/api/platform/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={"model": "platform:demo", "stream": True, "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 200
    assert "prompt_tokens" in response.text
    forwarded = _FakeClient.payloads[0]
    assert forwarded["stream_options"]["include_usage"] is True
    assert _audit_rows() == [("platform:demo", 5, 6, 200, "desktop")]


def test_disabled_account_does_not_call_upstream(gateway, monkeypatch):
    token = _login(gateway, monkeypatch)
    user = auth_records.get_user_by_email("user@example.com")
    set_tenant_status(user["id"], "disabled")
    response = gateway.post(
        "/api/platform/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={"model": "platform:demo", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 403
    assert _FakeClient.payloads == []


def test_regular_user_cannot_edit_platform_capabilities(gateway, monkeypatch):
    token = _login(gateway, monkeypatch)
    response = gateway.put(
        "/api/admin/platform-models",
        headers={"Authorization": f"Bearer {token}"},
        json={"models": []},
    )
    assert response.status_code == 403


def test_admin_saves_capability_table(gateway, monkeypatch):
    monkeypatch.setattr("src.config.settings.settings.key2api_sync_token", "")
    from src.server.auth.service import ensure_default_admins

    ensure_default_admins()
    logged = gateway.post(
        "/api/auth/login",
        json={"email": "admin@yinli.test", "password": "yinli-admin", "clientKind": "desktop"},
    )
    token = logged.json()["token"]
    saved = gateway.put(
        "/api/admin/platform-models",
        headers={"Authorization": f"Bearer {token}"},
        json={"models": [{
            "model": "demo",
            "label": "Demo",
            "capabilities": {"tools": True, "images": True, "thinking": False},
        }]},
    )
    assert saved.status_code == 200
    listed = gateway.get("/api/admin/platform-models", headers={"Authorization": f"Bearer {token}"})
    assert listed.json()["models"][0]["capabilities"]["images"] is True
    raw = json.dumps(listed.json())
    assert "k2a_" not in raw
