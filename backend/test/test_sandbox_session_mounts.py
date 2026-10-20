"""Sandbox 工作目錄與掛載只使用 Session 授權。"""

import pytest

from src.config.runtime_paths import session_artifacts_root
from src.sandbox.hosted_workspace import build_hosted_spec
from src.sandbox.session_mounts import resolve_sandbox_layout
from src.sessions.errors import SessionError
from src.tenancy.context import TenantContext, use_tenant


def test_sandbox_without_directory_uses_session_artifacts() -> None:
    layout = resolve_sandbox_layout({"sessionId": "ses-box", "workingDirectory": "", "allowedRoots": []})
    artifacts = session_artifacts_root("ses-box").resolve()
    assert layout.cwd == str(artifacts)
    assert layout.mounts[0]["source"] == str(artifacts)
    assert layout.mounts[0]["target"] == "/workspace"


def test_sandbox_external_directory_is_the_granted_mount(tmp_path) -> None:
    external = tmp_path / "external"
    external.mkdir()
    layout = resolve_sandbox_layout({
        "sessionId": "ses-box",
        "workingDirectory": str(external),
        "allowedRoots": [{"normalizedPath": str(external), "accessMode": "read_write"}],
    })
    assert layout.cwd == str(external.resolve())
    assert layout.mounts[0]["source"] == str(external.resolve())
    assert layout.mounts[0]["target"] == "/workspace"
    assert layout.mounts[0]["mode"] == "rw"


def test_hosted_container_mounts_only_the_tenant_grant() -> None:
    owner = TenantContext.hosted("usr_" + "ab" * 16)
    with use_tenant(owner):
        work = owner.data_root / "projects" / "box"
        work.mkdir(parents=True)
        layout = resolve_sandbox_layout({
            "sessionId": "ses-box",
            "workingDirectory": str(work),
            "allowedRoots": [{"normalizedPath": str(work), "accessMode": "read"}],
        })
        spec = build_hosted_spec(
            tenant_id=owner.tenant_id,
            project_id="box",
            agent_id="agent-box",
            project_root=work,
            mounts=list(layout.mounts),
        )
    assert layout.mounts[0]["mode"] == "ro"
    assert spec.binds[0].endswith(":/workspace:ro")
    assert "projects/box" in spec.binds[0]


def test_illegal_mount_is_rejected(tmp_path) -> None:
    with pytest.raises(SessionError) as root_error:
        resolve_sandbox_layout({
            "sessionId": "ses-box",
            "workingDirectory": "",
            "allowedRoots": [{"normalizedPath": "/", "accessMode": "read_write"}],
        })
    assert root_error.value.code == "illegal_mount"
    owner = TenantContext.hosted("usr_" + "cd" * 16)
    outside = tmp_path / "outside"
    outside.mkdir()
    with use_tenant(owner):
        with pytest.raises(SessionError) as foreign:
            resolve_sandbox_layout({
                "sessionId": "ses-box",
                "workingDirectory": str(outside),
                "allowedRoots": [{"normalizedPath": str(outside), "accessMode": "read_write"}],
            })
    assert foreign.value.code == "illegal_mount"
