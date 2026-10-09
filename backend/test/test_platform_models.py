"""平台模型目录、网关和桌面端转发。"""

from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.server.auth.desktop_account import clear_desktop_account, save_desktop_account
from src.server.auth.middleware import HostedSessionMiddleware
from src.server.auth.platform_chat import platform_chat_response
from src.server.auth.platform_models import router as platform_router
from src.server.auth.routes import auth_router
from src.server.local_access import LocalAccessMiddleware
from src.server.routes.chat.models import ChatMessage
from src.server.routes.chat.store import _load_store
from src.storage import database


@pytest.fixture()
def hosted_models(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "platform.db")
    monkeypatch.setattr(database, "_initialized", False)
    monkeypatch.setattr("src.config.settings.settings.serve_mode", "hosted")
    monkeypatch.setattr("src.config.settings.settings.auth_email_mode", "log")
    app = FastAPI()
    app.add_middleware(HostedSessionMiddleware)
    app.add_middleware(LocalAccessMiddleware)
    app.include_router(auth_router)
    app.include_router(platform_router)
    database.ensure_db()
    client = TestClient(app, client=("127.0.0.1", 50000), base_url="https://testserver")
    yield client
    database._initialized = False
    clear_desktop_account()


def _token(client: TestClient, monkeypatch) -> str:
    sent: list[str] = []

    def capture(_email: str, _purpose: str, code: str) -> None:
        sent.append(code)

    monkeypatch.setattr("src.server.auth.service.deliver_login_code", capture)
    client.post("/api/auth/register", json={"email": "user@example.com", "password": "password1"})
    client.post("/api/auth/verify", json={"email": "user@example.com", "code": sent[0]})
    from src.server.auth.ownership import assign_unowned
    from src.storage import auth_records

    user = auth_records.get_user_by_email("user@example.com")
    assign_unowned(user["id"])
    logged = client.post(
        "/api/auth/login",
        json={"email": "user@example.com", "password": "password1", "clientKind": "desktop"},
    )
    return logged.json()["token"]


def test_provided_catalog_hides_keys_and_requires_session(hosted_models, monkeypatch):
    client = hosted_models
    assert client.get("/api/models/provided").status_code == 401
    token = _token(client, monkeypatch)
    response = client.get("/api/models/provided", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    body = response.json()
    assert body["models"]
    assert all(item["id"].startswith("platform:") for item in body["models"])
    assert "test-only" not in response.text


def test_complete_writes_audit_not_chat(hosted_models, monkeypatch):
    client = hosted_models
    token = _token(client, monkeypatch)

    async def fake_invoke(model_config_id: str, text: str) -> str:
        assert model_config_id == "platform:deepseek-v4-flash"
        assert text == "你好"
        return "平台回复"

    monkeypatch.setattr("src.server.auth.platform_models.invoke_platform_model", fake_invoke)
    response = client.post(
        "/api/models/complete",
        headers={"Authorization": f"Bearer {token}"},
        json={"modelConfigId": "platform:deepseek-v4-flash", "text": "你好"},
    )
    assert response.status_code == 200
    assert "平台回复" in response.text
    with sqlite3.connect(database.DB_PATH) as conn:
        calls = conn.execute("SELECT model_config_id FROM platform_model_calls").fetchall()
        messages = conn.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0]
    assert calls == [("platform:deepseek-v4-flash",)]
    assert messages == 0


def test_desktop_platform_turn_stays_local(tmp_path, monkeypatch):
    import asyncio

    from fastapi import HTTPException
    from src.server.routes.chat.handlers.send import start_chat_run

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "desktop.db")
    monkeypatch.setattr(database, "_initialized", False)
    monkeypatch.setattr("src.config.settings.settings.serve_mode", "desktop")
    monkeypatch.setattr(
        "src.server.auth.desktop_account.ACCOUNT_FILE",
        tmp_path / "hosted-account.json",
    )
    database.ensure_db()
    clear_desktop_account()

    async def run() -> None:
        with pytest.raises(HTTPException) as missing:
            await platform_chat_response(ChatMessage(message="hi", modelConfigId="platform:deepseek-v4-flash"))
        assert missing.value.status_code == 401

        save_desktop_account(base_url="https://platform.example", token="desk-token", email="user@example.com")

        async def fetch(**kwargs):
            assert kwargs["token"] == "desk-token"
            return "来自平台"

        monkeypatch.setattr("src.server.auth.platform_chat.fetch_platform_reply", fetch)
        response = await platform_chat_response(
            ChatMessage(message="你好", modelConfigId="platform:deepseek-v4-flash", session_id="local-1"),
        )
        text = "".join([chunk async for chunk in response.body_iterator])
        assert "来自平台" in text
        store = _load_store()
        assert any(item.content == "来自平台" for item in store.messages["local-1"])

        seen: list[str | None] = []

        def resolve(model_id=None):
            seen.append(model_id)
            raise ValueError("stop-local")

        monkeypatch.setattr("src.server.routes.chat.handlers.send.resolve_model_binding", resolve)
        with pytest.raises(HTTPException) as local_error:
            await start_chat_run(ChatMessage(message="本地", modelConfigId="deepseek-v4-flash", session_id="local-2"))
        assert local_error.value.status_code == 422
        assert seen == ["deepseek-v4-flash"]

    asyncio.run(run())
    database._initialized = False
