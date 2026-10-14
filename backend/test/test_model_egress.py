"""托管模型地址不能指向本机或内网。"""

from __future__ import annotations

import pytest

from src.agents.model_factory import create_model_chain
from src.config.model_catalog import CatalogError, ModelEntry, ProviderConfig
from src.config.runtime_settings import ResolvedModel
from src.security.egress import EgressBlocked, assert_public_url_sync
from src.tenancy.context import TenantContext, use_tenant

pytest_plugins = ["test.hosted_support"]

USER = "usr_" + "c" * 32


def _resolved(base_url: str) -> ResolvedModel:
    provider = ProviderConfig(
        id="custom",
        name="Custom",
        adapter="openai",
        baseUrl=base_url,
        apiKey="sk-test",
    )
    entry = ModelEntry(id="m1", providerId="custom", model="m1", label="m1")
    return ResolvedModel(entry, provider)


def test_hosted_rejects_private_model_urls(hosted_env):
    with use_tenant(TenantContext.hosted(USER)):
        with pytest.raises(EgressBlocked):
            assert_public_url_sync("http://127.0.0.1:11434/v1", purpose="model")
        with pytest.raises(CatalogError, match="内网"):
            create_model_chain([], resolved_models=(_resolved("http://192.168.1.8/v1"),))
        assert_public_url_sync("https://1.1.1.1/v1", purpose="model")


def test_desktop_allows_local_model_urls(monkeypatch):
    monkeypatch.setattr("src.config.settings.settings.serve_mode", "desktop")
    assert_public_url_sync("http://127.0.0.1:11434/v1", purpose="model")
