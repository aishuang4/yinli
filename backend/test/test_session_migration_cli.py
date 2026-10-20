"""遷移 CLI 的子命令與退出碼。"""

from __future__ import annotations

import json

import pytest

from src.session_migration.__main__ import main
from src.session_migration.state import set_migration_flag
from src.storage import database


def test_status_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    set_migration_flag(False, schema_version_value=0)
    assert main(["status"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["cutover"] == "legacy"


def test_dry_run_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    set_migration_flag(False, schema_version_value=0)
    assert main(["dry-run"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] in {"dry_run", "fresh_cutover", "already_cutover"}


def test_run_exits_zero_for_fresh_tenant(capsys: pytest.CaptureFixture[str]) -> None:
    set_migration_flag(False, schema_version_value=0)
    with database._lock, database._connect() as conn:
        conn.execute("DELETE FROM projects")
        conn.execute("DELETE FROM workspaces")
        conn.commit()
    assert main(["run"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] in {"fresh_cutover", "already_cutover", "cutover"}


def test_rollback_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["rollback"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["readPath"] == "legacy"


def test_blocked_exits_two(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(
        "src.session_migration.__main__.run_tenant_migration",
        lambda **_kwargs: {"status": "blocked", "conflicts": []},
    )
    assert main(["run"]) == 2
    capsys.readouterr()


def test_exception_exits_one(monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(**_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("src.session_migration.__main__.run_tenant_migration", explode)
    assert main(["dry-run"]) == 1
