"""托管用户同步 key2api 后，平台模型使用该用户的 Key。"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.config.runtime_settings import resolve_model_binding
from src.server.auth.middleware import HostedSessionMiddleware
from src.tenancy.context import TenantContext, use_tenant
from src.server.auth.platform_models import router as platform_router
from src.server.auth.routes import auth_router
from src.server.local_access import LocalAccessMiddleware
from src.storage import auth_records, database


@pytest.fixture()
def hosted_sync(tmp_path, monkeypatch):
    monkeypatch.setattr("src.config.settings.settings.serve_mode", "hosted")
    monkeypatch.setattr("src.config.settings.settings.auth_email_mode", "log")
    monkeypatch.setattr("src.config.settings.settings.key2api_sync_token", "svc-token")
    monkeypatch.setattr("src.config.settings.settings.key2api_base_url", "http://key2api.test")
    calls: list[tuple] = []

    def fake_request(method, url, headers=None, json=None, params=None, timeout=None):
        calls.append((method, url, headers["Authorization"], json))
        if url.endswith("/accounts/sync"):
            return httpx.Response(200, json={
                "user_id": 12,
                "token_id": 34,
                "token": "k2a_testkey",
                "status": json["status"],
            })
        if url.endswith("/accounts/usage"):
            assert headers["Authorization"] == "Bearer svc-token"
            assert params["external_id"]
            return httpx.Response(200, json={
                "external_id": params["external_id"],
                "token_quota": 1000000,
                "used": 1200,
                "reserved": 0,
                "remaining": 998800,
            })
        if url.endswith("/v1/models"):
            assert headers["Authorization"] == "Bearer k2a_testkey"
            return httpx.Response(200, json={"object": "list", "data": [{"id": "demo-model"}]})
        raise AssertionError(url)

    monkeypatch.setattr("src.server.auth.key2api.httpx.request", fake_request)
    app = FastAPI()
    app.add_middleware(HostedSessionMiddleware)
    app.add_middleware(LocalAccessMiddleware)
    app.include_router(auth_router)
    app.include_router(platform_router)
    database.ensure_db()
    client = TestClient(app, client=("127.0.0.1", 50000), base_url="https://testserver")
    yield client, calls
    database.forget_db(database.db_path())


def _verify(client: TestClient, monkeypatch) -> dict:
    sent: list[str] = []
    monkeypatch.setattr(
        "src.server.auth.service.deliver_login_code",
        lambda _email, _purpose, code: sent.append(code),
    )
    client.post("/api/auth/register", json={"email": "user@example.com", "password": "password1"})
    verified = client.post("/api/auth/verify", json={"email": "user@example.com", "code": sent[0]})
    assert verified.status_code == 200
    assert "k2a_" not in verified.text
    return auth_records.get_user_by_email("user@example.com")


def test_verify_assigns_key_and_lists_only_platform_models(hosted_sync, monkeypatch):
    client, calls = hosted_sync
    user = _verify(client, monkeypatch)
    stored = auth_records.get_provider_key(user["id"])
    assert stored["token"] == "k2a_testkey"
    assert stored["status"] == "active"
    assert calls[0][1].endswith("/api/v1/internal/accounts/sync")
    assert calls[0][2] == "Bearer svc-token"
    assert calls[0][3]["external_id"] == user["id"]

    logged = client.post(
        "/api/auth/login",
        json={"email": "user@example.com", "password": "password1", "clientKind": "desktop"},
    )
    assert sum(1 for call in calls if call[1].endswith("/accounts/sync")) == 1
    response = client.get(
        "/api/models/provided",
        headers={"Authorization": f"Bearer {logged.json()['token']}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "key2api"
    assert body["models"] == [{
        "id": "platform:demo-model",
        "label": "demo-model",
        "providerName": "引力模型",
        "adapter": "openai",
        "capabilities": {"tools": True, "images": False, "thinking": False},
    }]
    assert "k2a_testkey" not in response.text

    with use_tenant(TenantContext.hosted(user["id"], role=user["role"])):
        binding = resolve_model_binding("platform:demo-model")
    assert binding.primary.base_url == "http://key2api.test/v1"
    assert binding.primary.default_model == "demo-model"
    assert binding.primary.api_key == "k2a_testkey"
    assert "k2a_testkey" not in repr(binding)
    usage = client.get(
        "/api/auth/usage",
        headers={"Authorization": f"Bearer {logged.json()['token']}"},
    )
    assert usage.status_code == 200
    assert usage.json() == {"available": True, "tokenQuota": 1000000, "used": 1200, "remaining": 998800}
    assert "k2a_testkey" not in usage.text
    assert "svc-token" not in usage.text
