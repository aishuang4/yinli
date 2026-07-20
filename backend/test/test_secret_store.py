"""模型 API Key 安全存储回归测试。"""

from __future__ import annotations

from copy import deepcopy
import json

import pytest

from src.config import runtime_settings, secret_store


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


def test_legacy_plaintext_key_migrates_before_database_cleanup(monkeypatch) -> None:
    """旧版明文 Key 先进入安全存储，再从 SQLite 配置中删除。"""

    persisted: dict[str, object] = {}
    stored = {
        "provider": "deepseek",
        "providerConfigs": {
            "deepseek": {
                "providerName": "DeepSeek",
                "apiKey": "sk-legacy-plaintext",
                "baseUrl": "https://api.deepseek.com",
                "defaultModel": "deepseek-v4-flash",
            },
        },
    }
    monkeypatch.setattr(
        runtime_settings,
        "upsert_json_record",
        lambda _namespace, _key, value: persisted.update(deepcopy(value)),
    )

    migrated = runtime_settings._migrate_legacy_api_keys(stored)

    assert secret_store.get_model_api_key("deepseek") == "sk-legacy-plaintext"
    assert "apiKey" not in migrated["providerConfigs"]["deepseek"]
    assert migrated["providerConfigs"]["deepseek"]["apiKeyConfigured"] is True
    assert "sk-legacy-plaintext" not in json.dumps(persisted)
    assert stored["providerConfigs"]["deepseek"]["apiKey"] == "sk-legacy-plaintext"


def test_legacy_plaintext_remains_when_secure_migration_fails(monkeypatch) -> None:
    """安全存储失败时不能删除旧 Key，避免升级导致凭据丢失。"""

    stored = {
        "provider": "deepseek",
        "providerConfigs": {"deepseek": {"apiKey": "sk-keep-me"}},
    }
    writes: list[dict] = []
    monkeypatch.setattr(
        runtime_settings,
        "set_model_api_key",
        lambda *_args: (_ for _ in ()).throw(secret_store.SecretStoreError("failed")),
    )
    monkeypatch.setattr(
        runtime_settings,
        "upsert_json_record",
        lambda _namespace, _key, value: writes.append(value),
    )

    with pytest.raises(secret_store.SecretStoreError):
        runtime_settings._migrate_legacy_api_keys(stored)

    assert stored["providerConfigs"]["deepseek"]["apiKey"] == "sk-keep-me"
    assert writes == []


def test_save_model_settings_never_persists_plaintext_key(monkeypatch) -> None:
    """新增 Key 只进入安全存储，公开响应和 SQLite 均不包含明文。"""

    records: dict[str, dict] = {}

    def fake_get_json_record(_namespace: str, key: str):
        value = records.get(key)
        return deepcopy(value) if value else None

    def fake_upsert_json_record(_namespace: str, key: str, value: dict):
        records[key] = deepcopy(value)

    monkeypatch.setattr(runtime_settings, "get_json_record", fake_get_json_record)
    monkeypatch.setattr(runtime_settings, "upsert_json_record", fake_upsert_json_record)

    response = runtime_settings.save_model_settings({
        "provider": "deepseek",
        "providerName": "DeepSeek",
        "baseUrl": "https://api.deepseek.com",
        "defaultModel": "deepseek-v4-flash",
        "fallbackModels": ["deepseek-v4-pro"],
        "customModels": [],
        "apiKey": "sk-new-plaintext",
    })

    assert secret_store.get_model_api_key("deepseek") == "sk-new-plaintext"
    assert response["apiKeyConfigured"] is True
    assert response["apiKeyPreview"] == "已安全保存"
    assert "sk-new-plaintext" not in json.dumps(response, ensure_ascii=False)
    assert "sk-new-plaintext" not in json.dumps(records, ensure_ascii=False)
    assert "apiKey" not in records[runtime_settings.MODEL_SETTINGS_KEY]["providerConfigs"]["deepseek"]

    cleared = runtime_settings.save_model_settings({
        "provider": "deepseek",
        "providerName": "DeepSeek",
        "baseUrl": "https://api.deepseek.com",
        "defaultModel": "deepseek-v4-flash",
        "fallbackModels": ["deepseek-v4-pro"],
        "customModels": [],
        "clearApiKey": True,
    })

    assert secret_store.get_model_api_key("deepseek") == ""
    assert cleared["apiKeyConfigured"] is False
    assert records[runtime_settings.MODEL_SETTINGS_KEY]["providerConfigs"]["deepseek"]["apiKeyConfigured"] is False


def test_public_settings_verify_all_provider_credentials(monkeypatch) -> None:
    """公开设置以安全凭据库为准，不能沿用 SQLite 中过期的已配置标志。"""

    stored = {
        "provider": "deepseek",
        "providerConfigs": {
            "deepseek": {"apiKeyConfigured": False},
            "openai": {"apiKeyConfigured": True},
        },
    }
    monkeypatch.setattr(
        runtime_settings,
        "get_json_record",
        lambda _namespace, _key: deepcopy(stored),
    )

    response = runtime_settings.get_model_settings_public()
    providers = {item["id"]: item for item in response["providers"]}

    assert providers["openai"]["apiKeyConfigured"] is False
    assert providers["openai"]["configured"] is False
