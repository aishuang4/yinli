"""Session 目錄白名單只含本 tenant 未歸檔目錄。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from src.server.main import app
from src.sessions.records import create_session
from src.sessions.service import archive_session
from src.tenancy.context import TenantContext, use_tenant


def test_archived_session_directory_is_omitted(tmp_path: Path) -> None:
    active = tmp_path / "active"
    archived = tmp_path / "archived"
    active.mkdir()
    archived.mkdir()
    kept = create_session(title="使用中", working_directory=str(active))
    gone = create_session(title="已歸檔", working_directory=str(archived))
    archive_session(gone["id"])
    client = TestClient(app)
    response = client.get("/api/sessions/directory-roots")
    assert response.status_code == 200
    roots = response.json()["roots"]
    assert str(active.resolve()) in roots
    assert str(archived.resolve()) not in roots
    assert kept["id"]


def test_other_tenant_directory_is_omitted(tmp_path: Path) -> None:
    local_dir = tmp_path / "local"
    other_dir = tmp_path / "other"
    local_dir.mkdir()
    other_dir.mkdir()
    create_session(title="本機", working_directory=str(local_dir))
    other = TenantContext.hosted("usr_" + "ab" * 16)
    with use_tenant(other):
        from src.config.runtime_paths import ensure_tenant_directories
        from src.storage import database

        ensure_tenant_directories()
        database.ensure_db()
        create_session(title="別人", working_directory=str(other_dir))
    client = TestClient(app)
    roots = client.get("/api/sessions/directory-roots").json()["roots"]
    assert str(local_dir.resolve()) in roots
    assert str(other_dir.resolve()) not in roots
