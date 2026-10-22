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
from src.storage.engine import database
from src.storage.control import auth_records


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


class _Ledger:
    """按邮箱记住平台账号。同一邮箱再次同步交回原 Key，账号不在则新开一把。"""

    def __init__(self) -> None:
        self.accounts: dict[str, dict] = {}
        self.calls: list[tuple] = []
        self.reply_status: str | None = None
        self._seq = 0

    def drop(self, email: str) -> None:
        self.accounts.pop(email, None)

    def request(self, method, url, headers=None, json=None, params=None, timeout=None):
        self.calls.append((method, url, headers["Authorization"], json, params))
        if url.endswith("/accounts/sync"):
            email = json["email"]
            account = self.accounts.get(email)
            if account is None:
                self._seq += 1
                account = {
                    "user_id": self._seq,
                    "token_id": self._seq + 100,
                    "token": f"k2a_ledger{self._seq}",
                    "used": 1200,
                }
                self.accounts[email] = account
            account["external_id"] = json["external_id"]
            replied = self.reply_status or json["status"]
            return httpx.Response(200, json={
                "user_id": account["user_id"],
                "token_id": account["token_id"],
                "token": account["token"],
                "status": replied,
            })
        if url.endswith("/accounts/usage"):
            matched = [
                item for item in self.accounts.values()
                if item.get("external_id") == params["external_id"]
            ]
            assert len(matched) == 1
            return httpx.Response(200, json={
                "external_id": params["external_id"],
                "token_quota": 1000000,
                "used": matched[0]["used"],
                "reserved": 0,
                "remaining": 998800,
            })
        if url.endswith("/v1/models"):
            return httpx.Response(200, json={"object": "list", "data": [{"id": "demo-model"}]})
        raise AssertionError(url)


@pytest.fixture()
def hosted_ledger(hosted_sync, monkeypatch):
    client, _calls = hosted_sync
    ledger = _Ledger()
    monkeypatch.setattr("src.server.auth.key2api.httpx.request", ledger.request)
    return client, ledger


def _login(client: TestClient) -> dict:
    logged = client.post(
        "/api/auth/login",
        json={"email": "user@example.com", "password": "password1", "clientKind": "desktop"},
    )
    assert logged.status_code == 200
    assert "k2a_" not in logged.text
    return logged.json()


def test_reregister_reuses_platform_key_for_new_user_id(hosted_ledger, monkeypatch, capsys):
    client, ledger = hosted_ledger
    first = _verify(client, monkeypatch)
    old_token = auth_records.get_provider_key(first["id"])["token"]
    auth_records.delete_account(first["id"], first["email"])

    second = _verify(client, monkeypatch)
    stored = auth_records.get_provider_key(second["id"])
    assert second["id"] != first["id"]
    assert stored["token"] == old_token
    assert stored["status"] == "active"
    assert auth_records.get_provider_key(first["id"]) is None

    logged = _login(client)
    usage = client.get(
        "/api/auth/usage",
        headers={"Authorization": f"Bearer {logged['token']}"},
    )
    assert usage.status_code == 200
    assert usage.json()["used"] == 1200
    assert "k2a_" not in usage.text
    usage_calls = [item for item in ledger.calls if str(item[1]).endswith("/accounts/usage")]
    assert usage_calls[-1][4]["external_id"] == second["id"]
    assert old_token not in capsys.readouterr().out


def test_reregister_creates_key_when_platform_account_is_gone(hosted_ledger, monkeypatch):
    client, ledger = hosted_ledger
    first = _verify(client, monkeypatch)
    old_token = auth_records.get_provider_key(first["id"])["token"]
    auth_records.delete_account(first["id"], first["email"])
    ledger.drop(first["email"])

    second = _verify(client, monkeypatch)
    stored = auth_records.get_provider_key(second["id"])
    assert stored["token"] != old_token
    assert stored["token"].startswith("k2a_")
    assert "k2a_" not in client.get("/api/auth/config").text


def test_status_mismatch_does_not_store_key(hosted_ledger, monkeypatch, capsys):
    client, ledger = hosted_ledger
    ledger.reply_status = "disabled"
    user = _verify(client, monkeypatch)
    assert auth_records.get_provider_key(user["id"]) is None
    assert "k2a_" not in capsys.readouterr().out
