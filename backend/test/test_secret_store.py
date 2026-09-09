"""模型 API Key 安全存储回归测试。"""

from __future__ import annotations


import pytest

from src.config import secret_store


@pytest.fixture(autouse=True)
def memory_secret_store(monkeypatch):
    """测试期间使用内存凭据库，禁止访问真实系统 Keychain。"""

    monkeypatch.setenv("AGENT_WORKSTATION_SECRET_STORE", "memory")
    secret_store._cache.clear()
    secret_store._memory_secrets.clear()
    yield
    secret_store._cache.clear()
    secret_store._memory_secrets.clear()


def test_memory_secret_store_round_trip() -> None:
    """安全存储支持保存、读取和删除。"""

    secret_store.set_model_api_key("deepseek", "sk-secret")
    assert secret_store.get_model_api_key("deepseek") == "sk-secret"

    secret_store.delete_model_api_key("deepseek")
    assert secret_store.get_model_api_key("deepseek") == ""


def test_non_desktop_macos_uses_memory_store(monkeypatch) -> None:
    """Web/源码模式不能因 macOS Keychain 授权缺失而阻断设置页启动。"""

    monkeypatch.delenv("AGENT_WORKSTATION_DESKTOP_MODE", raising=False)
    monkeypatch.delenv("AGENT_WORKSTATION_SECRET_STORE", raising=False)
    monkeypatch.setattr(secret_store.sys, "platform", "darwin")

    assert secret_store._backend_name() == "memory"


def test_desktop_macos_uses_keychain(monkeypatch) -> None:
    """Electron 桌面后端继续使用 macOS Keychain。"""

    monkeypatch.setenv("AGENT_WORKSTATION_DESKTOP_MODE", "1")
    monkeypatch.delenv("AGENT_WORKSTATION_SECRET_STORE", raising=False)
    monkeypatch.setattr(secret_store.sys, "platform", "darwin")

    assert secret_store._backend_name() == "keychain"


def test_secret_store_rejects_multiline_values() -> None:
    """Key 不能借助换行符破坏系统凭据输入协议。"""

    with pytest.raises(secret_store.SecretStoreError):
        secret_store.set_model_api_key("deepseek", "first-line\nsecond-line")


def test_keychain_write_keeps_secret_out_of_process_arguments(monkeypatch) -> None:
    """Keychain 写入通过伪终端 stdin 传 Key，不能出现在进程参数中。"""

    captured: dict[str, object] = {}

    def fake_run(args, value):
        captured["args"] = args
        captured["input"] = value
        return 0

    monkeypatch.setattr(secret_store, "_run_security_password_prompt", fake_run)
    secret_store._keychain_write("model-provider:deepseek", "sk-sensitive")

    assert "sk-sensitive" not in captured["args"]
    assert captured["input"] == "sk-sensitive"
