"""hosted 测试工具：托管环境夹具、造用户、取租户上下文。

沙箱替换成 FakeSandbox 要等 sandbox/provider.py，任务组 6 再接入。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.tenancy.context import TenantContext


def make_hosted_root(tmp_path: Path) -> Path:
    root = tmp_path / "hosted-root"
    root.mkdir(exist_ok=True)
    return root


@pytest.fixture()
def hosted_env(tmp_path, monkeypatch):
    """把进程切到 hosted：独立数据根、log 邮件、测试主密钥。

    控制面模块在任务组 3 落地后再接入；当前只保证环境与租户上下文可用。
    """

    from cryptography.fernet import Fernet

    from src.config import runtime_paths
    from src.storage import control_db

    root = make_hosted_root(tmp_path)
    control_db.forget_control_db()
    monkeypatch.setattr(runtime_paths, "USER_DATA_ROOT", root)
    monkeypatch.setenv("AGENT_WORKSTATION_DATA_DIR", str(root))
    monkeypatch.setenv("AGENT_WORKSTATION_SECRET_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr("src.config.settings.settings.serve_mode", "hosted")
    monkeypatch.setattr("src.config.settings.settings.auth_email_mode", "log")
    monkeypatch.setattr("src.config.settings.settings.key2api_sync_token", "")
    control_db.ensure_control_db()
    from src.sandbox.fake import FakeSandbox
    from src.sandbox.provider import set_sandbox_provider

    set_sandbox_provider(FakeSandbox())
    yield root
    set_sandbox_provider(None)
    control_db.forget_control_db()


def make_user(client, email: str) -> tuple[str, str]:
    """注册、验证、登录；返回 (token, user_id)。"""

    from src.server.auth import service
    from src.storage import auth_records

    sent: list[str] = []
    original = service.deliver_login_code
    service.deliver_login_code = lambda _email, _purpose, code: sent.append(code)
    try:
        client.post("/api/auth/register", json={"email": email, "password": "password1"})
        client.post("/api/auth/verify", json={"email": email, "code": sent[0]})
        logged = client.post(
            "/api/auth/login",
            json={"email": email, "password": "password1", "clientKind": "desktop"},
        )
    finally:
        service.deliver_login_code = original
    user = auth_records.get_user_by_email(email)
    return logged.json()["token"], user["id"]


def tenant_of(user_id: str, *, role: str = "user") -> TenantContext:
    return TenantContext.hosted(user_id, role=role)


def seed_legacy_project() -> None:
    """測試裡仍引用歷史 default Project 的租戶庫補一行，不走正式啟動。"""

    from src.config.runtime_paths import tenant_root
    from src.storage import database

    database.ensure_db()
    if database.get_workspace("default") is None:
        database.create_workspace({"id": "default", "name": "歷史工作區"})
    if database.get_project("default") is None:
        root = tenant_root() / "historical-project"
        root.mkdir(parents=True, exist_ok=True)
        database.create_project({
            "id": "default",
            "name": "歷史項目",
            "root_path": str(root),
            "workspace" + "_id": "default",
        })
