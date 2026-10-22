"""平台模型目录来自 key2api，桌面端 platform: 对话走正常 Run。"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.server.auth.middleware import HostedSessionMiddleware
from src.server.auth.platform_models import router as platform_router
from src.server.auth.routes import auth_router
from src.server.local_access import LocalAccessMiddleware
from src.server.routes.chat.handlers.send import start_chat_run
from src.server.routes.chat.models import ChatMessage
from src.storage.engine import database


@pytest.fixture()
def hosted_models(monkeypatch):
    monkeypatch.setattr("src.config.settings.settings.serve_mode", "hosted")
    monkeypatch.setattr("src.config.settings.settings.auth_email_mode", "log")
    monkeypatch.setattr("src.config.settings.settings.key2api_sync_token", "svc-token")
    app = FastAPI()
    app.add_middleware(HostedSessionMiddleware)
    app.add_middleware(LocalAccessMiddleware)
    app.include_router(auth_router)
    app.include_router(platform_router)
    database.ensure_db()
    client = TestClient(app, client=("127.0.0.1", 50000), base_url="https://testserver")
    yield client
    database.forget_db(database.db_path())


def _token(client: TestClient, monkeypatch) -> str:
    sent: list[str] = []
    monkeypatch.setattr(
        "src.server.auth.service.deliver_login_code",
        lambda _email, _purpose, code: sent.append(code),
    )
    client.post("/api/auth/register", json={"email": "user@example.com", "password": "password1"})
    client.post("/api/auth/verify", json={"email": "user@example.com", "code": sent[0]})
    logged = client.post(
        "/api/auth/login",
        json={"email": "user@example.com", "password": "password1", "clientKind": "desktop"},
    )
    return logged.json()["token"]


def test_provided_uses_platform_layer_not_deployment_catalog(hosted_models, monkeypatch):
    from src.config.model_catalog import ModelCatalog, ModelEntry, ProviderConfig, _write
    from src.storage.control import auth_records
    from src.tenancy.context import TenantContext, use_tenant

    client = hosted_models
    assert client.get("/api/models/provided").status_code == 401
    token = _token(client, monkeypatch)
    user = auth_records.get_user_by_email("user@example.com")
    with use_tenant(TenantContext.hosted(user["id"], role=user["role"])):
        _write(ModelCatalog(
            providers=(ProviderConfig(
                id="openai", name="OpenAI", adapter="openai",
                baseUrl="https://api.openai.com/v1", apiKey="sk-should-not-leak",
            ),),
            models=(ModelEntry(id="gpt", providerId="openai", model="gpt", label="gpt"),),
            defaultModelId="gpt",
        ))
    monkeypatch.setattr("src.server.auth.key2api.ensure_provider_key", lambda user: None)
    monkeypatch.setattr(
        "src.server.auth.key2api.list_platform_models",
        lambda user_id: [{"id": "platform:demo", "label": "demo", "providerName": "引力模型", "adapter": "openai"}],
    )
    response = client.get("/api/models/provided", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "key2api"
    assert body["models"][0]["id"] == "platform:demo"
    assert "sk-should-not-leak" not in response.text
    assert "gpt" not in {item["id"] for item in body["models"]}


def test_desktop_platform_model_enters_normal_run(monkeypatch):
    monkeypatch.setattr("src.config.settings.settings.serve_mode", "desktop")
    database.ensure_db()

    async def run() -> None:
        with pytest.raises(HTTPException) as missing:
            await start_chat_run(ChatMessage(message="hi", modelConfigId="platform:demo", session_id="local-1", project_slug="default"))
        assert missing.value.status_code == 422
        assert "登录引力云端" in missing.value.detail

        seen: list[str | None] = []

        def resolve(model_id=None, **kwargs):
            seen.append(model_id)
            raise ValueError("stop-local")

        monkeypatch.setattr("src.server.routes.chat.handlers.send.resolve_model_binding", resolve)
        with pytest.raises(HTTPException) as local_error:
            await start_chat_run(ChatMessage(message="本地", modelConfigId="deepseek-v4-flash", session_id="local-2", project_slug="default"))
        assert local_error.value.status_code == 422
        assert seen == ["deepseek-v4-flash"]
        with pytest.raises(HTTPException):
            await start_chat_run(ChatMessage(message="平台", modelConfigId="platform:demo", session_id="local-3", project_slug="default"))
        assert seen[-1] == "platform:demo"

    asyncio.run(run())
    database.forget_db(database.db_path())
