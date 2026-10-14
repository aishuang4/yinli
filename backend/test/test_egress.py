"""托管出站：私网、元数据、映射地址和重定向都要挡住；桌面不拦截。"""

from __future__ import annotations

import pytest

from src.security.egress import EgressBlocked, assert_public_url_sync, assert_redirect
from src.tenancy.context import TenantContext, use_tenant

pytest_plugins = ["test.hosted_support"]

USER = "usr_" + "e" * 32


def test_hosted_blocks_private_metadata_and_mapped_addresses(hosted_env):
    with use_tenant(TenantContext.hosted(USER)):
        for url in (
            "http://127.0.0.1/",
            "http://192.168.1.2/v1",
            "http://169.254.169.254/",
            "http://[::ffff:127.0.0.1]/",
        ):
            with pytest.raises(EgressBlocked):
                assert_public_url_sync(url, purpose="model")
        with pytest.raises(EgressBlocked):
            assert_redirect("http://10.1.1.1/secret")
        assert_public_url_sync("https://1.1.1.1/v1", purpose="model")


def test_allowlist_and_desktop_skip(hosted_env, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSTATION_EGRESS_ALLOW_HOSTS", "10.9.9.9")
    with use_tenant(TenantContext.hosted(USER)):
        assert_public_url_sync("http://10.9.9.9/v1", purpose="model")
    monkeypatch.setattr("src.config.settings.settings.serve_mode", "desktop")
    assert_public_url_sync("http://127.0.0.1:11434/v1", purpose="model")
