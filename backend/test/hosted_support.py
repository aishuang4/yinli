"""hosted 测试工具：托管环境夹具、造用户、取租户上下文。

任务组 1 先实现到能建控制面为止；任务组 3 补全沙箱替换与租户生命周期。
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

    root = make_hosted_root(tmp_path)
    monkeypatch.setattr(runtime_paths, "USER_DATA_ROOT", root)
    monkeypatch.setenv("AGENT_WORKSTATION_DATA_DIR", str(root))
    monkeypatch.setenv("AGENT_WORKSTATION_SECRET_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr("src.config.settings.settings.serve_mode", "hosted")
    monkeypatch.setattr("src.config.settings.settings.auth_email_mode", "log")
    return root


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
        logged = client.post("/api/auth/login", json={"email": email, "password": "password1"})
    finally:
        service.deliver_login_code = original
    user = auth_records.get_user_by_email(email)
    return logged.json()["token"], user["id"]


def tenant_of(user_id: str, *, role: str = "user") -> TenantContext:
    return TenantContext.hosted(user_id, role=role)
