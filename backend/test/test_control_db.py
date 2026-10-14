"""控制面库：只在 hosted 创建，账号表与租户表在同一库。"""

from __future__ import annotations

import sqlite3

import pytest

from src.storage import control_db, control_records
from src.tenancy.context import TENANT_ID_PATTERN

pytest_plugins = ["test.hosted_support"]


HOSTED_A = "usr_" + "a" * 32
HOSTED_B = "usr_" + "b" * 32


def test_desktop_refuses_control_db(tmp_path, monkeypatch):
    from src.config import runtime_paths

    monkeypatch.setattr(runtime_paths, "USER_DATA_ROOT", tmp_path)
    monkeypatch.setattr("src.config.settings.settings.serve_mode", "desktop")
    control_db.forget_control_db()
    with pytest.raises(RuntimeError, match="托管模式"):
        control_db.ensure_control_db()
    assert not (tmp_path / "control" / "control.db").exists()


def test_hosted_control_db_holds_accounts_tenants_and_audit(hosted_env):
    control_db.forget_control_db()
    created = control_records.insert_tenant(HOSTED_A)
    assert created["status"] == "active"
    assert created["quota"].disk_mb == 2048
    control_records.insert_tenant(HOSTED_B)
    control_records.set_tenant_status(HOSTED_B, "disabled")
    control_records.record_platform_call(
        user_id=HOSTED_A,
        model_config_id="platform:demo",
        client_kind="web",
        prompt_tokens=3,
        completion_tokens=1,
        status=200,
    )

    path = control_db.control_db_path()
    assert path.is_relative_to(hosted_env / "control")
    with sqlite3.connect(path) as conn:
        names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"auth_users", "auth_sessions", "auth_email_codes", "auth_provider_keys", "tenants", "platform_call_audit"} <= names

    active = control_records.list_active_tenants()
    assert [item.tenant_id for item in active] == [HOSTED_A]
    assert TENANT_ID_PATTERN.match(active[0].tenant_id)
    assert control_records.get_tenant(HOSTED_B)["status"] == "disabled"
    again = control_records.insert_tenant(HOSTED_B)
    assert again["status"] == "disabled"


def test_tenant_database_has_no_auth_tables(hosted_env):
    from src.storage import database
    from src.tenancy.context import TenantContext, use_tenant
    from src.tenancy.lifecycle import ensure_tenant

    ensure_tenant({
        "id": HOSTED_A,
        "email": "a@example.com",
        "role": "user",
        "status": "active",
    })
    with use_tenant(TenantContext.hosted(HOSTED_A)):
        database.ensure_db()
        path = database.db_path()
    with sqlite3.connect(path) as conn:
        names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert not any(name.startswith("auth_") for name in names)
    assert path != control_db.control_db_path()
