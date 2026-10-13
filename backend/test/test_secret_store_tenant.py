"""托管租户密钥：加密落盘、租户隔离、缺少主密钥时拒绝启动。"""

from __future__ import annotations

import stat

import pytest

from src.config import secret_store
from src.config.settings import HostedConfigError
from src.tenancy.context import TenantContext, use_tenant

pytest_plugins = ["test.hosted_support"]

USER_A = "usr_" + "a" * 32
USER_B = "usr_" + "b" * 32


@pytest.fixture(autouse=True)
def _clear_secret_cache():
    secret_store._cache.clear()
    yield
    secret_store._cache.clear()


def test_hosted_secret_file_is_not_plaintext(hosted_env):
    with use_tenant(TenantContext.hosted(USER_A)):
        secret_store.set_model_api_key("openai", "sk-tenant-plain")
        secret_store.set_named_secret("secret:tool.token", "named-plain-token")
        folder = secret_store.secret_dir()
        blobs = [path.read_bytes() for path in folder.rglob("*") if path.is_file()]
    assert blobs
    joined = b"\n".join(blobs)
    assert b"sk-tenant-plain" not in joined
    assert b"named-plain-token" not in joined
    assert not (folder / "catalog").exists()
    for path in folder.rglob("*.enc"):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_same_provider_id_stays_inside_each_tenant(hosted_env):
    with use_tenant(TenantContext.hosted(USER_A)):
        secret_store.set_model_api_key("openai", "sk-a")
    with use_tenant(TenantContext.hosted(USER_B)):
        secret_store.set_model_api_key("openai", "sk-b")
        assert secret_store.get_model_api_key("openai") == "sk-b"
    with use_tenant(TenantContext.hosted(USER_A)):
        assert secret_store.get_model_api_key("openai") == "sk-a"


def test_hosted_startup_fails_without_master_key(hosted_env, monkeypatch):
    monkeypatch.delenv("AGENT_WORKSTATION_SECRET_KEY", raising=False)
    with pytest.raises(HostedConfigError, match="SECRET_KEY"):
        secret_store.assert_hosted_secret_key()
