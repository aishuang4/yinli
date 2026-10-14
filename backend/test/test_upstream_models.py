"""已保存的供应商才能拉取上游模型名，Key 不离开服务端。"""

from __future__ import annotations

import pytest

from src.config import model_catalog as store

pytest_plugins = ["test.hosted_support"]


def test_upstream_models_use_saved_key_and_skip_duplicates(monkeypatch):
    store._write(store.ModelCatalog(
        defaultModelId="",
        providers=[
            store.ProviderConfig(
                id="gate",
                name="网关",
                adapter="openai",
                baseUrl="https://models.example/v1",
                apiKey="sk-hidden",
            ),
        ],
        models=[],
    ))
    seen = {}

    class Response:
        status_code = 200

        def json(self):
            return {"data": [{"id": "gpt-a"}, {"id": "gpt-a"}, {"id": "gpt-b"}, {}]}

    def fake_get(url, headers, timeout, follow_redirects):
        seen["url"] = url
        seen["auth"] = headers.get("Authorization")
        seen["timeout"] = timeout
        seen["redirects"] = follow_redirects
        return Response()

    monkeypatch.setattr("httpx.get", fake_get)
    assert store.list_provider_upstream_models("gate") == ["gpt-a", "gpt-b"]
    assert seen["url"] == "https://models.example/v1/models"
    assert seen["auth"] == "Bearer sk-hidden"
    assert seen["redirects"] is False


def test_hosted_catalog_stores_upstream_names_without_key(hosted_env, monkeypatch):
    from src.tenancy.context import TenantContext, use_tenant
    from src.tenancy.lifecycle import ensure_tenant

    user_id = "usr_" + "a" * 32
    ensure_tenant({
        "id": user_id,
        "email": "a@example.com",
        "role": "user",
        "status": "active",
    })
    monkeypatch.setattr("src.config.settings.settings.key2api_sync_token", "svc")
    monkeypatch.setattr(
        "src.server.auth.key2api.list_platform_models",
        lambda user_id: [{"label": "gpt-5.6-sol", "id": "platform:gpt-5.6-sol"}],
    )
    with use_tenant(TenantContext.hosted(user_id)):
        store.ensure_hosted_upstream_catalog()
        catalog, _etag = store.read_catalog()
        store.ensure_hosted_upstream_catalog()
    assert [item.model for item in catalog.models] == ["gpt-5.6-sol"]
    assert catalog.default_model_id == "upstream-gpt-5.6-sol"
    assert catalog.providers[-1].id == "yinli-platform"
    assert catalog.providers[-1].name == "系统内置模型"
    assert catalog.providers[-1].api_key == ""


def test_upstream_models_reject_loopback():
    store._write(store.ModelCatalog(
        defaultModelId="",
        providers=[
            store.ProviderConfig(
                id="local",
                name="本机",
                adapter="openai",
                baseUrl="http://127.0.0.1:9/v1",
                apiKey="sk",
            ),
        ],
        models=[],
    ))
    with pytest.raises(store.CatalogError, match="内网"):
        store.list_provider_upstream_models("local")
