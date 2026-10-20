"""Session 目錄授權、hosted 枚舉與 Artifact 工作區。"""

from fastapi.testclient import TestClient

from src.config.runtime_paths import session_artifacts_root
from src.server.main import app
from src.sessions.directories import docker_mount_snapshot, replace_directory_grant, session_work_directory
from src.sessions.errors import SessionError
from src.sessions.records import create_session, freeze_run_snapshot, get_run_snapshot
from src.storage.database import _connect, _lock
from src.tenancy.context import TenantContext, use_tenant
import pytest


def test_internal_artifact_workspace_is_downloadable() -> None:
    session = create_session(session_id="sesart")
    work = session_work_directory(session["id"])
    assert work == session_artifacts_root(session["id"])
    (work / "note.txt").write_text("preview", encoding="utf-8")
    with TestClient(app) as client:
        response = client.get(f"/api/sessions/{session['id']}/artifacts/note.txt")
    assert response.status_code == 200
    assert response.text == "preview"


def test_directory_update_keeps_old_snapshot_and_blocks_active_run(tmp_path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    session = create_session(session_id="sesdir", working_directory=str(first))
    snapshot = freeze_run_snapshot(
        run_id="run-dir",
        session_id=session["id"],
        working_directory=str(first),
        allowed_roots=[{"normalizedPath": str(first), "accessMode": "read_write"}],
        resources={},
        model_snapshot={},
        approval_snapshot={},
    )
    updated = replace_directory_grant(session["id"], str(second), access_mode="read_write", source="user")
    assert get_run_snapshot("run-dir") == snapshot
    assert updated["directoryGrantVersion"] == 1
    with _lock, _connect() as conn:
        conn.execute(
            """
            INSERT INTO chat_runs (id, session_id, status, created_at, updated_at)
            VALUES ('run-live', 'sesdir', 'running', 't', 't')
            """
        )
        conn.commit()
    with pytest.raises(SessionError) as exc:
        replace_directory_grant(session["id"], str(first))
    assert exc.value.code == "session_has_active_runs"
    assert get_run_snapshot("run-dir")["workingDirectory"] == str(first)


def test_hosted_mount_rejects_home_other_tenant_and_forged_path(tmp_path) -> None:
    owner = TenantContext.hosted("usr_" + "ab" * 16)
    other = TenantContext.hosted("usr_" + "cd" * 16)
    with use_tenant(other):
        foreign = other.data_root / "projects"
        foreign.mkdir(parents=True, exist_ok=True)
    with use_tenant(owner):
        create_session(session_id="seshost")
        allowed = owner.data_root
        allowed.mkdir(parents=True, exist_ok=True)
        replace_directory_grant("seshost", str(allowed), source="hosted")
        mounts = docker_mount_snapshot("seshost")
        assert mounts[0]["source"] == str(allowed.resolve())
        forged = tmp_path / "forged"
        forged.mkdir()
        with pytest.raises(SessionError) as home:
            replace_directory_grant("seshost", str(forged))
        assert home.value.code == "hosted_path_rejected"
        from pathlib import Path
        with pytest.raises(SessionError) as host_home:
            replace_directory_grant("seshost", str(Path.home()))
        assert host_home.value.code == "hosted_path_rejected"
        with pytest.raises(SessionError) as foreign_error:
            replace_directory_grant("seshost", str(foreign))
        assert foreign_error.value.code == "tenant_mismatch"
