"""租户上下文：desktop 默认 local、hosted fail closed、嵌套与任务传播。"""

from __future__ import annotations

import asyncio

import pytest

from src.config import runtime_paths
from src.tenancy import context as tenant_context
from src.tenancy.context import (
    LOCAL_TENANT_ID,
    TenantContext,
    TenantMissing,
    current_tenant,
    current_tenant_or_none,
    is_hosted_mode,
    spawn_in_tenant,
    use_tenant,
)
from src.tenancy.registry import TenantScoped, evict_all, evict_tenant, register_tenant_cleanup

HOSTED_ID = "usr_" + "a" * 32
HOSTED_ID_B = "usr_" + "b" * 32


def _drop_fixture_tenant():
    """autouse local_tenant 夹具设置了上下文；本模块的用例需要"没有上下文"的起点。"""

    return tenant_context._TENANT.set(None)


def test_desktop_defaults_to_local_tenant(monkeypatch):
    monkeypatch.setattr("src.tenancy.context.settings.serve_mode", "desktop")
    token = _drop_fixture_tenant()
    try:
        ctx = current_tenant()
        assert ctx.tenant_id == LOCAL_TENANT_ID
        assert ctx.kind == "local"
        assert not ctx.is_hosted
        assert ctx.data_root == runtime_paths.USER_DATA_ROOT
        assert current_tenant() is ctx  # 进程内单例
    finally:
        tenant_context._TENANT.reset(token)


def test_hosted_without_context_fails_closed(monkeypatch):
    monkeypatch.setattr("src.tenancy.context.settings.serve_mode", "hosted")
    token = _drop_fixture_tenant()
    try:
        assert is_hosted_mode()
        with pytest.raises(TenantMissing):
            current_tenant()
        assert current_tenant_or_none() is None
    finally:
        tenant_context._TENANT.reset(token)


def test_hosted_context_requires_pattern():
    with pytest.raises(ValueError):
        TenantContext.hosted("../x")
    with pytest.raises(ValueError):
        TenantContext.hosted("usr_short")
    ctx = TenantContext.hosted(HOSTED_ID, role="admin")
    assert ctx.is_hosted
    assert ctx.user_id == HOSTED_ID
    assert ctx.user_role == "admin"
    assert ctx.data_root == runtime_paths.tenants_root() / HOSTED_ID


def test_use_tenant_nests_and_restores(monkeypatch):
    monkeypatch.setattr("src.tenancy.context.settings.serve_mode", "hosted")
    token = _drop_fixture_tenant()
    try:
        outer = TenantContext.hosted(HOSTED_ID)
        inner = TenantContext.hosted(HOSTED_ID_B)
        with use_tenant(outer):
            assert current_tenant() is outer
            with use_tenant(inner):
                assert current_tenant() is inner
            assert current_tenant() is outer
        with pytest.raises(TenantMissing):
            current_tenant()
    finally:
        tenant_context._TENANT.reset(token)


def test_spawn_in_tenant_propagates_context(monkeypatch):
    monkeypatch.setattr("src.tenancy.context.settings.serve_mode", "hosted")
    token = _drop_fixture_tenant()
    try:
        ctx = TenantContext.hosted(HOSTED_ID)

        async def read() -> str:
            return current_tenant().tenant_id

        async def main() -> None:
            # 任务在 use_tenant 块外创建，但必须读到自己的租户。
            task = spawn_in_tenant(ctx, read())
            assert await task == HOSTED_ID
            with pytest.raises(TenantMissing):
                current_tenant()

        asyncio.run(main())
    finally:
        tenant_context._TENANT.reset(token)


def test_local_singleton_rebuilds_when_data_root_changes(monkeypatch, tmp_path):
    monkeypatch.setattr("src.tenancy.context.settings.serve_mode", "desktop")
    token = _drop_fixture_tenant()
    try:
        first = current_tenant()
        monkeypatch.setattr(runtime_paths, "USER_DATA_ROOT", tmp_path / "elsewhere")
        second = current_tenant()
        assert second.data_root != first.data_root
        assert second.data_root == tmp_path / "elsewhere"
    finally:
        tenant_context._TENANT.reset(token)


def test_tenant_scoped_lazy_loads_per_tenant(monkeypatch):
    monkeypatch.setattr("src.tenancy.context.settings.serve_mode", "hosted")
    built: list[str] = []
    scoped = TenantScoped(lambda: built.append(current_tenant().tenant_id) or {"tenant": current_tenant().tenant_id})
    with use_tenant(TenantContext.hosted(HOSTED_ID)):
        first = scoped.get()
        assert first["tenant"] == HOSTED_ID
        assert scoped.get() is first
        assert scoped.peek(HOSTED_ID) is first
        scoped.replace({"tenant": "replaced"})
        assert scoped.get() == {"tenant": "replaced"}
    with use_tenant(TenantContext.hosted(HOSTED_ID_B)):
        assert scoped.get()["tenant"] == HOSTED_ID_B
    assert built == [HOSTED_ID, HOSTED_ID_B]


def test_background_run_and_pending_interaction_stay_inside_tenant():
    import asyncio

    from src.server.routes.chat.background_runs import (
        ChatBackgroundRun,
        _runs,
        get_chat_background_run,
    )
    from src.server.routes.chat.models import (
        PENDING_INTERACTIONS,
        PendingInteraction,
        get_pending_interaction,
    )

    run = ChatBackgroundRun(id="run-tenant-a", session_id="s", tenant_id=HOSTED_ID)
    _runs[run.id] = run
    PENDING_INTERACTIONS["hitl-tenant-a"] = PendingInteraction(
        id="hitl-tenant-a",
        type="confirm",
        session_id="s",
        agent_id="default",
        agent_name="助手",
        model_name="test",
        reply_id="reply",
        tool_calls=[],
        message="确认",
        tenant_id=HOSTED_ID,
    )
    try:
        async def check() -> None:
            with use_tenant(TenantContext.hosted(HOSTED_ID_B)):
                assert await get_chat_background_run("run-tenant-a") is None
                assert get_pending_interaction("hitl-tenant-a") is None
            with use_tenant(TenantContext.hosted(HOSTED_ID)):
                assert await get_chat_background_run("run-tenant-a") is not None
                assert get_pending_interaction("hitl-tenant-a") is not None

        asyncio.run(check())
    finally:
        _runs.pop("run-tenant-a", None)
        PENDING_INTERACTIONS.pop("hitl-tenant-a", None)


def test_evict_tenant_clears_scoped_and_dict_caches(monkeypatch):
    monkeypatch.setattr("src.tenancy.context.settings.serve_mode", "hosted")
    scoped = TenantScoped(dict)
    dict_cache: dict[tuple[str, str], str] = {}

    def cleanup(tenant_id: str | None) -> None:
        if tenant_id is None:
            dict_cache.clear()
        else:
            for key in [key for key in dict_cache if key[0] == tenant_id]:
                del dict_cache[key]

    register_tenant_cleanup("test_dict_cache", cleanup)
    with use_tenant(TenantContext.hosted(HOSTED_ID)):
        scoped.get()["x"] = 1
    with use_tenant(TenantContext.hosted(HOSTED_ID_B)):
        scoped.get()["y"] = 2
    dict_cache[(HOSTED_ID, "default")] = "a"
    dict_cache[(HOSTED_ID_B, "default")] = "b"

    evict_tenant(HOSTED_ID)
    assert scoped.peek(HOSTED_ID) is None
    assert scoped.peek(HOSTED_ID_B) is not None
    assert dict_cache == {(HOSTED_ID_B, "default"): "b"}

    evict_all()
    assert scoped.peek(HOSTED_ID_B) is None
    assert dict_cache == {}
