"""桌面云端账号：令牌进密钥存储，过期提示重新登录，桌面会话 30 天滑动。"""

from __future__ import annotations

import json
from datetime import timedelta

import httpx
import pytest

from src.config.secret_store import get_named_secret
from src.server.auth.cloud_client import (
    CLOUD_SECRET,
    binding,
    clear_model_cache,
    list_models_sync,
    load_account,
    save_account,
)
from src.server.auth.desktop_account import account_path
from src.storage.control import auth_records

pytest_plugins = ["test.hosted_support"]


def test_legacy_token_moves_out_of_the_account_file():
    path = account_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "baseUrl": "https://cloud.example",
        "email": "user@example.com",
        "token": "desk-token",
    }), encoding="utf-8")
    account = load_account()
    assert account is not None
    assert account.token == "desk-token"
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert "token" not in saved
    assert get_named_secret(CLOUD_SECRET) == "desk-token"


def test_expired_cloud_session_asks_for_login(monkeypatch):
    save_account("https://cloud.example", "user@example.com", "desk-token")

    def fake_get(url, headers=None, timeout=None):
        assert headers["Authorization"] == "Bearer desk-token"
        return httpx.Response(401, json={"detail": "需要登录"})

    monkeypatch.setattr("src.server.auth.cloud_client.httpx.get", fake_get)
    clear_model_cache()
    assert list_models_sync() == []
    account = load_account()
    assert account is not None
    assert account.status == "expired"
    assert account.email == "user@example.com"
    with pytest.raises(Exception, match="重新登录"):
        binding("platform:demo")


def test_logged_out_desktop_hides_platform_models():
    clear_model_cache()
    assert list_models_sync() == []
    with pytest.raises(Exception, match="登录引力云端"):
        binding("platform:demo")


def test_desktop_session_slides_for_thirty_days(hosted_env):
    created = auth_records.create_session(
        user_id="usr_" + "d" * 32,
        raw_token="desktop-token",
        client_kind="desktop",
    )
    first = auth_records.get_session_by_token("desktop-token")
    assert first is not None
    auth_records.touch_session(created["id"])
    second = auth_records.get_session_by_token("desktop-token")
    assert second is not None
    from src.storage.control.auth_records import _parse

    span = _parse(second["expiresAt"]) - _parse(first["expiresAt"])
    assert span >= timedelta(0)
    remaining = _parse(second["expiresAt"]) - _parse(second["lastSeenAt"])
    assert remaining >= timedelta(days=30) - timedelta(seconds=2)
    assert remaining <= timedelta(days=30) + timedelta(seconds=2)


def test_hosted_startup_requires_key2api_token(hosted_env, monkeypatch):
    from src.config.settings import HostedConfigError, assert_hosted_platform_config

    monkeypatch.setattr("src.config.settings.settings.key2api_sync_token", "")
    with pytest.raises(HostedConfigError, match="KEY2API_SYNC_TOKEN"):
        assert_hosted_platform_config()
