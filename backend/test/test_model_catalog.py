"""模型目录迁移、完整连接绑定及真实 HTTP 请求回归。"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
from agentscope.message import UserMsg

from src.config import model_catalog as store
from src.config.runtime_settings import (
    binding_for_model,
    get_effective_model_settings,
    model_binding_scope,
    resolve_model_binding,
)


def configure(
    url_a="http://127.0.0.1:1/v1", url_b="http://127.0.0.1:2/v1", *, fallback=False
):
    catalog = store.ModelCatalog(
        defaultModelId="a-model",
        providers=[
            store.ProviderConfig(
                id="a",
                name="OpenAI",
                adapter="openai",
                baseUrl=url_a,
                apiKey="fake-key-a",
            ),
            store.ProviderConfig(
                id="b",
                name="DeepSeek",
                adapter="deepseek",
                baseUrl=url_b,
                apiKey="fake-key-b",
            ),
        ],
        models=[
            store.ModelEntry(
                id="a-model",
                providerId="a",
                model="shared-model",
                label="A",
                fallbackModelIds=["b-model"] if fallback else [],
            ),
            store.ModelEntry(
                id="b-model", providerId="b", model="shared-model", label="B"
            ),
        ],
    )
    store._write(catalog)
    return catalog


@contextmanager
def fake_provider(*, failure=False):
    """标准 OpenAI-compatible HTTP 服务，记录实际请求而非 mock 工厂参数。"""
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(
                {
                    "path": self.path,
                    "auth": self.headers.get("Authorization"),
                    "body": body,
                }
            )
            if failure:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(
                    b'{"error":{"message":"test failure","type":"invalid_request_error"}}'
                )
                return
            self.send_response(200)
            self.send_header(
                "Content-Type",
                "text/event-stream" if body.get("stream") else "application/json",
            )
            self.end_headers()
            if body.get("stream"):
                for choice in (
                    {
                        "delta": {"role": "assistant", "content": "ok"},
                        "finish_reason": None,
                    },
                    {"delta": {}, "finish_reason": "stop"},
                ):
                    chunk = {
                        "id": "test",
                        "object": "chat.completion.chunk",
                        "created": 1,
                        "model": body["model"],
                        "choices": [{"index": 0, **choice}],
                    }
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                self.wfile.write(b"data: [DONE]\n\n")
            else:
                self.wfile.write(
                    json.dumps(
                        {
                            "id": "test",
                            "object": "chat.completion",
                            "created": 1,
                            "model": body["model"],
                            "choices": [
                                {
                                    "index": 0,
                                    "message": {"role": "assistant", "content": "ok"},
                                    "finish_reason": "stop",
                                }
                            ],
                            "usage": {
                                "prompt_tokens": 1,
                                "completion_tokens": 1,
                                "total_tokens": 2,
                            },
                        }
                    ).encode()
                )

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(
        target=lambda: server.serve_forever(poll_interval=0.01), daemon=True
    )
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_actual_http_switching_both_keys_and_identical_model_names():
    from src.agents.model_factory import create_model

    with fake_provider() as (url_a, requests_a), fake_provider() as (url_b, requests_b):
        configure(url_a, url_b)

        async def run():
            for ident in ("a-model", "b-model", "a-model", "b-model"):
                with model_binding_scope(resolve_model_binding(ident)):
                    await create_model("shared-model", stream=False)(
                        messages=[UserMsg(name="user", content="test")]
                    )

        asyncio.run(run())
        for requests, key in ((requests_a, "fake-key-a"), (requests_b, "fake-key-b")):
            assert len(requests) == 2
            assert all(r["path"] == "/v1/chat/completions" for r in requests)
            assert all(r["auth"] == f"Bearer {key}" for r in requests)
            assert all(r["body"]["model"] == "shared-model" for r in requests)


def test_actual_fallback_uses_own_provider_and_key():
    from src.agents.model_factory import create_model_chain

    with (
        fake_provider(failure=True) as (url_a, requests_a),
        fake_provider() as (url_b, requests_b),
    ):
        configure(url_a, url_b, fallback=True)

        async def run():
            with model_binding_scope(resolve_model_binding("a-model")):
                chain = create_model_chain(["shared-model"])
                result = await chain(messages=[UserMsg(name="user", content="test")])
                return [chunk async for chunk in result]

        assert asyncio.run(run())
        assert requests_a[0]["auth"] == "Bearer fake-key-a"
        assert requests_b[0]["auth"] == "Bearer fake-key-b"


def test_run_snapshot_survives_file_edit_and_child_tasks():
    configure()
    binding = resolve_model_binding("b-model")
    public = store.public_catalog()
    public["providers"][1]["apiKey"] = "replacement-key"
    public["defaultModelId"] = "a-model"
    etag = public.pop("etag")
    store.save_catalog(public, etag)

    async def child():
        return get_effective_model_settings().api_key

    async def run():
        with model_binding_scope(binding):
            assert get_effective_model_settings().provider == "b"
            assert await asyncio.create_task(child()) == "fake-key-b"

    asyncio.run(run())
    assert resolve_model_binding("b-model").primary.api_key == "replacement-key"


def test_masking_clear_key_and_optimistic_conflict():
    configure()
    public = store.public_catalog()
    assert "fake-key" not in json.dumps(public)
    etag = public.pop("etag")
    public["providers"][1]["apiKey"] = ""
    result = store.save_catalog(public, etag)
    assert resolve_model_binding("a-model").primary.api_key == "fake-key-a"
    assert result["providers"][1]["apiKeyConfigured"] is False
    with pytest.raises(store.CatalogError, match="API Key"):
        resolve_model_binding("b-model")


def test_read_catalog_accepts_public_catalog_fields():
    configure()
    public = store.public_catalog()
    public["providers"][0]["apiKeyConfigured"] = True
    public.pop("etag")
    public["modelsPath"] = "/tmp/models.json"
    assert store._parse(public).default_model_id == "a-model"


def test_file_errors_never_restore_old_credentials(monkeypatch):
    configure()
    store.read_catalog()
    store.models_path().write_text('{"apiKey":"secret-in-bad-json"', encoding="utf-8")
    with pytest.raises(store.CatalogError) as error:
        resolve_model_binding()
    assert "secret-in-bad-json" not in str(error.value)
    store.models_path().unlink()
    with pytest.raises(store.CatalogError, match="不存在"):
        store.read_catalog()


def test_validate_references_and_disabled_models():
    catalog = configure()
    payload = catalog.model_dump(mode="json", by_alias=True)
    payload["models"][0]["fallbackModelIds"] = ["b-model"]
    payload["models"][1]["fallbackModelIds"] = ["a-model"]
    with pytest.raises(store.CatalogError):
        store._parse(payload)
    payload["models"][1]["fallbackModelIds"] = []
    payload["providers"][1]["enabled"] = False
    store._write(store._parse(payload))
    with pytest.raises(store.CatalogError, match="停用"):
        resolve_model_binding("a-model")


def test_explicit_subagent_name_must_be_unambiguous():
    from src.config.runtime_settings import binding_for_explicit_model

    configure()
    # 主模型同名属于明确继承；其他显式名称必须唯一。
    with model_binding_scope(resolve_model_binding("a-model")):
        assert binding_for_model("b-model").primary.provider == "b"
        with pytest.raises(store.CatalogError, match="多个供应商"):
            binding_for_explicit_model("shared-model")
        with pytest.raises(store.CatalogError):
            binding_for_model("missing-model")


def test_migration_preserves_instances_and_does_not_repeat(monkeypatch):
    from src.config import secret_store
    from src.storage.engine import database

    records = {
        "model": {
            "provider": "openai",
            "providerConfigs": {
                "openai": {
                    "defaultModel": "MyCaseSensitiveModel",
                    "baseUrl": "http://localhost:1234/v1",
                    "apiKeyConfigured": True,
                },
                "deepseek": {"defaultModel": "deepseek-chat", "apiKeyConfigured": True},
            },
        }
    }
    monkeypatch.setattr(
        database, "get_json_record", lambda namespace, key: records.get(key)
    )
    monkeypatch.setattr(
        database,
        "upsert_json_record",
        lambda namespace, key, data: records.__setitem__(key, data),
    )
    monkeypatch.setattr(
        secret_store,
        "get_model_api_key",
        lambda ident: {"openai": "old-openai", "deepseek": "old-deepseek"}.get(
            ident, ""
        ),
    )
    store.models_path().unlink()
    catalog, _ = store.read_catalog()
    assert catalog.default_model_id == "openai:MyCaseSensitiveModel"
    assert resolve_model_binding().primary.base_url == "http://localhost:1234/v1"
    assert (
        resolve_model_binding("deepseek:deepseek-chat").primary.api_key
        == "old-deepseek"
    )
    monkeypatch.setattr(
        secret_store,
        "get_model_api_key",
        lambda ident: pytest.fail("迁移后不应读取 Keychain"),
    )
    assert store.read_catalog()[0] == catalog


def test_atomic_write_failure_preserves_previous_file(monkeypatch):
    configure()
    before = store.models_path().read_bytes()
    public = store.public_catalog()
    etag = public.pop("etag")
    monkeypatch.setattr(
        store.os,
        "replace",
        lambda *_: (_ for _ in ()).throw(PermissionError("occupied")),
    )
    with pytest.raises(store.CatalogError, match="保存失败"):
        store.save_catalog(public, etag)
    assert store.models_path().read_bytes() == before
    assert not list(store.models_path().parent.glob(".models-*.tmp"))


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode 位只适用于 macOS/Linux")
def test_file_is_private():
    configure()
    assert store.models_path().stat().st_mode & 0o777 == 0o600


@pytest.mark.skipif(os.name != "nt", reason="Windows CI 检查受限 ACL")
def test_windows_file_has_protected_owner_acl():
    import subprocess

    configure()
    subprocess.run(
        ["icacls", str(store.models_path()), "/grant", "*S-1-1-0:(R)"],
        capture_output=True,
        check=True,
    )
    # 模拟启动时读取外部带有 Everyone 显式授权的文件。
    store._secured_windows_files.clear()
    store.read_catalog()
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            (
                "$acl = Get-Acl -LiteralPath $env:YINLI_TEST_FILE; "
                "$sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value; "
                "@{protected=$acl.AreAccessRulesProtected; rules=@($acl.Access | ForEach-Object { "
                "@{sid=$_.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value; inherited=$_.IsInherited} }); owner=$sid} | ConvertTo-Json -Depth 4"
            ),
        ],
        env={**os.environ, "YINLI_TEST_FILE": str(store.models_path())},
        capture_output=True,
        text=True,
        check=True,
    )
    acl = json.loads(result.stdout)
    assert acl["protected"] is True
    assert acl["rules"] and all(
        rule["sid"] == acl["owner"] and not rule["inherited"] for rule in acl["rules"]
    )


def test_factory_cache_changes_when_provider_key_changes():
    from src.agents.factory import clear_all_agents, get_or_create_agent

    configure()
    clear_all_agents()
    try:
        with model_binding_scope(resolve_model_binding("a-model")):
            first = get_or_create_agent(
                agent_id="cache-test", include_default_tools=False
            )
            assert (
                get_or_create_agent(agent_id="cache-test", include_default_tools=False)
                is first
            )
        public = store.public_catalog()
        public["providers"][0]["apiKey"] = "new-key"
        etag = public.pop("etag")
        store.save_catalog(public, etag)
        with model_binding_scope(resolve_model_binding("a-model")):
            second = get_or_create_agent(
                agent_id="cache-test", include_default_tools=False
            )
        assert second is not first
        assert first.model.credential.api_key.get_secret_value() == "fake-key-a"
        assert second.model.credential.api_key.get_secret_value() == "new-key"
    finally:
        clear_all_agents()


def test_background_run_and_approval_resume_reuse_original_binding():
    from src.server.routes.chat.background_runs import (
        relay_chat_background_run_events,
        start_chat_background_run,
    )

    configure()
    seen = []

    async def run_case():
        ready = asyncio.Event()

        async def source():
            await ready.wait()
            seen.append(get_effective_model_settings().api_key)
            if False:
                yield

        run = await start_chat_background_run(
            session_id="model-snapshot-test",
            factory=source,
            persist=False,
            model_binding=resolve_model_binding("b-model"),
        )
        public = store.public_catalog()
        public["providers"][1]["apiKey"] = "changed-between-runs"
        etag = public.pop("etag")
        store.save_catalog(public, etag)
        ready.set()
        await run.async_task

        async def resume():
            seen.append(get_effective_model_settings().api_key)
            if False:
                yield

        async for _ in relay_chat_background_run_events(run.id, resume()):
            pass
        assert "fake-key" not in json.dumps(run.model_metadata)

    asyncio.run(run_case())
    assert seen == ["fake-key-b", "fake-key-b"]


def _process_save(path, payload, etag, output):
    """spawn 子进程验证锁文件与原子替换；只传递测试凭据。"""
    from pathlib import Path

    store.models_path = lambda: Path(path)
    try:
        store.save_catalog(payload, etag)
        output.put("saved")
    except store.CatalogConflict:
        output.put("conflict")


def test_concurrent_process_save_only_one_revision_wins():
    import multiprocessing

    configure()
    public = store.public_catalog()
    etag = public.pop("etag")
    context = multiprocessing.get_context("spawn")
    output = context.Queue()
    processes = [
        context.Process(
            target=_process_save, args=(str(store.models_path()), public, etag, output)
        )
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=15)
        if process.is_alive():
            process.terminate()
            process.join()
        assert process.exitcode == 0
    assert sorted(output.get(timeout=2) for _ in processes) == ["conflict", "saved"]


def test_unreadable_legacy_key_does_not_delete_old_configuration(monkeypatch):
    from src.config import secret_store
    from src.storage.engine import database

    old = {
        "provider": "deepseek",
        "providerConfigs": {"deepseek": {"apiKeyConfigured": True}},
    }
    monkeypatch.setattr(database, "get_json_record", lambda *_: old)

    def fail(_):
        raise secret_store.SecretStoreError("unavailable")

    monkeypatch.setattr(secret_store, "get_model_api_key", fail)
    catalog = store._migrate()
    assert [p.id for p in catalog.providers] == ["deepseek"]
    assert [m.model for m in catalog.models] == ["deepseek-v4-flash", "deepseek-v4-pro"]
    assert catalog.default_model_id == "deepseek:deepseek-v4-flash"
    assert next(p for p in catalog.providers if p.id == "deepseek").api_key == ""
    assert old["providerConfigs"]["deepseek"]["apiKeyConfigured"] is True
