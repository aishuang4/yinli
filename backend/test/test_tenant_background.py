"""两个租户的定时任务各自落在自己的库里。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from src.config.model_catalog import CatalogError
from src.scheduler.service import ScheduledJobService
from src.scheduler.settings import set_scheduled_jobs_settings
from src.scheduler.ticker import JobScheduler, reset_job_scheduler
from src.storage import database
from src.tenancy.context import TenantContext, use_tenant
from src.tenancy.lifecycle import ensure_tenant
from test.hosted_support import seed_legacy_project

pytest_plugins = ["test.hosted_support"]

NOW = datetime(2026, 8, 21, 10, 0, 1, tzinfo=timezone.utc)
USER_A = {"id": "usr_" + "a" * 32, "email": "a@example.com", "role": "user", "status": "active"}
USER_B = {"id": "usr_" + "b" * 32, "email": "b@example.com", "role": "user", "status": "active"}


def _confirm(service: ScheduledJobService, name: str):
    job = service.create_job(
        workspace_id="default",
        project_id="default",
        name=name,
        agent_id="default",
        schedule_kind="weekly",
        schedule_expr={"weekdays": ["fri"], "at": "18:00"},
        timezone="Asia/Shanghai",
        frozen_prompt="写周报",
    )
    return service.confirm(job.id, now=datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc))


def test_tick_writes_each_tenant_session(hosted_env, monkeypatch):
    ensure_tenant(USER_A)
    ensure_tenant(USER_B)
    for user in (USER_A, USER_B):
        with use_tenant(TenantContext.hosted(user["id"])):
            seed_legacy_project()
            set_scheduled_jobs_settings(enabled=True, capability_gate=False)
            _confirm(ScheduledJobService(), user["id"])

    async def fake_start(req):
        from src.tenancy.context import current_tenant

        tenant_id = current_tenant().tenant_id
        database.insert_chat_session_if_absent({
            "id": f"sess-{tenant_id[-4:]}",
            "title": tenant_id,
            "agent_id": "default",
            "agent_name": "助手",
            "mode": "agent",
            "workspace_id": "default",
            "project_id": "default",
            "kind": "scheduled_job",
            "created_at": "2026-08-21T10:00:00+00:00",
            "updated_at": "2026-08-21T10:00:00+00:00",
        })
        return f"run-{req.firing_id}"

    async def fake_await(chat_run_id, *, timeout_seconds):
        _ = chat_run_id, timeout_seconds
        return "completed"

    monkeypatch.setattr("src.scheduler.firing.start_scheduled_chat_run", fake_start)
    monkeypatch.setattr("src.scheduler.firing.await_scheduled_chat_run", fake_await)
    monkeypatch.setattr("src.scheduler.firing._scan_outputs", lambda job, firing: {"files": []})
    reset_job_scheduler()
    scheduler = JobScheduler()

    async def run() -> None:
        stats = await scheduler.tick(now=NOW)
        assert stats["claimed"] == 2
        await asyncio.sleep(0.05)

    asyncio.run(run())
    with use_tenant(TenantContext.hosted(USER_A["id"])):
        assert database.get_chat_session("sess-" + USER_A["id"][-4:]) is not None
        assert database.get_chat_session("sess-" + USER_B["id"][-4:]) is None
    with use_tenant(TenantContext.hosted(USER_B["id"])):
        assert database.get_chat_session("sess-" + USER_B["id"][-4:]) is not None
        assert database.get_chat_session("sess-" + USER_A["id"][-4:]) is None
    reset_job_scheduler()


def test_disabled_tenant_is_not_ticked(hosted_env):
    from src.storage import control_records
    from src.tenancy.lifecycle import disable_tenant

    ensure_tenant(USER_A)
    ensure_tenant(USER_B)
    for user in (USER_A, USER_B):
        with use_tenant(TenantContext.hosted(user["id"])):
            seed_legacy_project()
            set_scheduled_jobs_settings(enabled=True, capability_gate=False)
            _confirm(ScheduledJobService(), user["id"])
    disable_tenant(USER_B["id"])
    reset_job_scheduler()
    scheduler = JobScheduler()

    async def run() -> dict:
        return await scheduler.tick(now=NOW)

    stats = asyncio.run(run())
    assert stats["claimed"] == 1
    assert USER_B["id"] not in {item.tenant_id for item in control_records.list_active_tenants()}
    reset_job_scheduler()


def test_platform_binding_uses_tenant_user(hosted_env, monkeypatch):
    from src.config.runtime_settings import resolve_model_binding
    from src.storage import auth_records

    ensure_tenant(USER_A)
    auth_records.save_provider_key(
        user_id=USER_A["id"],
        token="k2a_secret",
        key2api_user_id="k2a-user",
        key2api_token_id="k2a-token",
        status="active",
    )
    monkeypatch.setattr("src.config.settings.settings.key2api_sync_token", "svc")
    monkeypatch.setattr("src.server.auth.key2api.base_url", lambda: "http://platform.test")
    with use_tenant(TenantContext.hosted(USER_A["id"], role="user")):
        binding = resolve_model_binding("platform:demo")
    assert binding.primary.api_key == "k2a_secret"
    with use_tenant(TenantContext.hosted(USER_B["id"], role="user")):
        with pytest.raises(CatalogError, match="平台模型暂不可用"):
            resolve_model_binding("platform:demo")


def test_empty_default_uses_first_platform_model(hosted_env, monkeypatch):
    from src.config.runtime_paths import shared_root
    from src.config.runtime_settings import resolve_model_binding
    from src.storage import auth_records

    ensure_tenant(USER_A)
    auth_records.save_provider_key(
        user_id=USER_A["id"],
        token="k2a_secret",
        key2api_user_id="k2a-user",
        key2api_token_id="k2a-token",
        status="active",
    )
    monkeypatch.setattr("src.server.auth.key2api.base_url", lambda: "http://platform.test")
    monkeypatch.setattr(
        "src.server.auth.key2api.list_platform_models",
        lambda user_id: [{"id": "platform:first", "label": "first"}],
    )
    shared = shared_root()
    shared.mkdir(parents=True, exist_ok=True)
    (shared / "platform-models.json").write_text(
        '{"version":1,"models":[{"model":"first","capabilities":{"images":true}}]}',
        encoding="utf-8",
    )
    with use_tenant(TenantContext.hosted(USER_A["id"], role="user")):
        binding = resolve_model_binding()
    assert binding.primary.default_model == "first"
    assert binding.primary.api_key == "k2a_secret"
    assert len(binding.chain) == 1
    assert binding.primary.entry.capabilities.images is True


def test_hosted_without_any_model_reports_catalog_error(hosted_env, monkeypatch):
    from src.config.runtime_settings import resolve_model_binding

    ensure_tenant(USER_A)
    monkeypatch.setattr("src.server.auth.key2api.list_platform_models", lambda user_id: [])
    with use_tenant(TenantContext.hosted(USER_A["id"], role="user")):
        with pytest.raises(CatalogError, match="没有可用模型"):
            resolve_model_binding()
