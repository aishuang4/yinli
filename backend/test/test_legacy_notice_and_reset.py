"""封存提示与开发机清库。"""
from __future__ import annotations


import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from src.cli.main import main
from src.config import runtime_paths
from src.server.main import app
from src.storage.engine.database import (
    db_path,
    ensure_db,
)
from src.storage.engine.legacy_archive import archive_legacy_layout, pending_legacy_notice
from src.storage.engine.maintenance import DevResetRefused, reset_dev_database


def _plant_old_database(data: Path) -> None:
    data.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(data / "agent_workstation.db")
    conn.execute("CREATE TABLE workspaces (id TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()


def test_archive_writes_a_pending_notice(tmp_path: Path):
    data = tmp_path / "data"
    _plant_old_database(data)
    dest = archive_legacy_layout(data)
    notice = pending_legacy_notice(data)
    assert dest is not None
    assert notice["pending"] is True
    assert notice["path"] == str(dest)
    assert notice["title"] == "历史对话已封存"
    assert "不保留旧对话" in str(notice["message"])


def test_legacy_notice_api_can_be_acknowledged():
    data = runtime_paths.data_root()
    _plant_old_database(data)
    with TestClient(app) as client:
        pending = client.get("/api/db/legacy-notice")
        assert pending.status_code == 200
        assert pending.json()["pending"] is True
        acked = client.post("/api/db/legacy-notice/ack")
        assert acked.status_code == 200
        assert acked.json()["pending"] is False
        assert client.get("/api/db/legacy-notice").json()["pending"] is False


def test_reset_dev_removes_live_database_and_keeps_backups():
    ensure_db()
    live = db_path()
    assert live.is_file()
    kept = runtime_paths.tenant_root() / "backups" / "legacy-keep"
    kept.mkdir(parents=True)
    (kept / "agent_workstation.db").write_text("archived", encoding="utf-8")

    removed = reset_dev_database()

    assert live in removed
    assert not live.exists()
    assert (kept / "agent_workstation.db").read_text(encoding="utf-8") == "archived"
    assert main(["db", "reset", "--dev"]) == 0


def test_reset_dev_refuses_hosted(monkeypatch):
    monkeypatch.setattr("src.tenancy.context.settings.serve_mode", "hosted")
    ensure_db()
    live = db_path()
    try:
        reset_dev_database()
    except DevResetRefused as exc:
        assert "托管模式" in str(exc)
    else:
        raise AssertionError("hosted reset should be refused")
    assert live.is_file()
    assert main(["db", "reset", "--dev"]) == 2
    assert live.is_file()
