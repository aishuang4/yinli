"""第 12 節：新用戶不建立預設 Project，舊寫入回 410，外部隱藏目錄不再由運行時建立。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from src.config import runtime_paths
from src.server.main import app
from src.server.routes.sessions import post_session, SessionCreateBody
from src.session_migration.legacy_fields import LEGACY_SCOPE_FIELDS
from src.storage import database


def test_fresh_user_only_creates_tenant_root(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(runtime_paths, "USER_DATA_ROOT", tmp_path)
    monkeypatch.setattr(runtime_paths, "USING_CUSTOM_DATA_ROOT", True)
    runtime_paths._ensured_tenants.clear()
    db_path = tmp_path / "fresh.db"
    monkeypatch.setattr(
        "src.config.settings.settings.database_url",
        "sqlite+aiosqlite:///" + str(db_path),
    )
    database.forget_db(db_path)
    runtime_paths.ensure_tenant_directories()
    database.ensure_db()

    assert tmp_path.is_dir()
    assert not (tmp_path / "workspace").exists()
    assert not (tmp_path / "projects" / "default").exists()
    assert database.get_workspace("default") is None
    assert database.get_project("default") is None
    database.forget_db(db_path)


def test_legacy_scope_fields_do_not_change_session_or_directory(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "fresh.db"
    monkeypatch.setattr(
        "src.config.settings.settings.database_url",
        "sqlite+aiosqlite:///" + str(db_path),
    )
    database.forget_db(db_path)
    database.ensure_db()
    legacy_key = next(item for item in LEGACY_SCOPE_FIELDS if item.startswith("p") and item.endswith("Id"))
    with pytest.raises(HTTPException) as exc_info:
        post_session(SessionCreateBody.model_validate({"title": "舊欄位", legacy_key: "other", "rootPath": "/"}))
    assert exc_info.value.status_code == 410
    with database._lock, database._connect() as conn:
        count = conn.execute("SELECT COUNT(*) FROM chat_sessions").fetchone()[0]
    assert count == 0
    database.forget_db(db_path)


def test_runtime_sources_do_not_create_external_hidden_state() -> None:
    root = Path(__file__).resolve().parents[1] / "src"
    allowed = ("session_migration/",)
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        relative = path.relative_to(root).as_posix()
        if relative.startswith(allowed) or "__pycache__" in relative:
            continue
        text = path.read_text(encoding="utf-8")
        if "SESSION_DIRECTORY_NAME /" in text or "JOB_DIRECTORY_NAME /" in text:
            offenders.append(relative)
    assert offenders == []


def test_workspace_write_route_returns_410() -> None:
    with TestClient(app) as client:
        response = client.post("/api/workspaces", json={"name": "不再建立"})
    assert response.status_code == 410
    assert response.json()["detail"]["code"] == "legacy_workspace_retired"
