"""第 11 節遷移執行：catalog、Session 回填、檔案搬移、切換和回滾。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.session_migration.catalog_migrate import migrate_catalogs
from src.session_migration.cutover import attempt_cutover
from src.session_migration.file_stage import stage_file
from src.session_migration.legacy_cleanup import plan_legacy_cleanup
from src.session_migration.owner_backfill import backfill_owners
from src.session_migration.rollback import rollback_to_legacy
from src.session_migration.session_backfill import backfill_session_context


def test_same_catalog_merges_and_conflict_blocks(tmp_path: Path) -> None:
    left = tmp_path / "ws-a" / "profiles"
    right = tmp_path / "ws-b" / "profiles"
    other = tmp_path / "ws-c" / "profiles"
    left.mkdir(parents=True)
    right.mkdir(parents=True)
    other.mkdir(parents=True)
    (left / "agent.md").write_text("same\n", encoding="utf-8")
    (right / "agent.md").write_text("same\n", encoding="utf-8")
    (other / "agent.md").write_text("different\n", encoding="utf-8")
    destination = tmp_path / "global"
    first = migrate_catalogs([left, right], destination, tmp_path / "report")
    second = migrate_catalogs([left, right], destination, tmp_path / "report")
    assert first["conflicts"] == []
    assert (destination / "agent.md").read_text(encoding="utf-8") == "same\n"
    assert second["written"] == []
    blocked = migrate_catalogs([left, other], destination, tmp_path / "blocked")
    assert blocked["conflicts"]
    assert blocked["written"] == []
    assert (destination / "agent.md").read_text(encoding="utf-8") == "same\n"


def test_missing_or_archived_project_does_not_invent_directory(tmp_path: Path) -> None:
    root = tmp_path / "real"
    root.mkdir()
    conn = sqlite3.connect(tmp_path / "history.db")
    conn.executescript(
        """
        CREATE TABLE projects (id TEXT, root_path TEXT, status TEXT);
        CREATE TABLE chat_sessions (
            id TEXT, project_id TEXT, working_directory TEXT DEFAULT '', directory_grant_version INTEGER DEFAULT 0
        );
        CREATE TABLE project_resource_bindings (
            project_id TEXT, resource_type TEXT, resource_id TEXT, enabled INTEGER,
            PRIMARY KEY (project_id, resource_type, resource_id)
        );
        CREATE TABLE session_resource_grants (
            session_id TEXT, resource_type TEXT, resource_id TEXT, enabled INTEGER,
            config_override TEXT, grant_version INTEGER,
            PRIMARY KEY (session_id, resource_type, resource_id)
        );
        """
    )
    conn.execute("INSERT INTO projects VALUES ('live', ?, 'active')", (str(root),))
    conn.execute("INSERT INTO projects VALUES ('old', ?, 'archived')", (str(root),))
    conn.execute("INSERT INTO chat_sessions (id, project_id) VALUES ('kept', 'live')")
    conn.execute("INSERT INTO chat_sessions (id, project_id) VALUES ('gone', 'missing')")
    conn.execute("INSERT INTO chat_sessions (id, project_id) VALUES ('archived', 'old')")
    conn.execute("INSERT INTO project_resource_bindings VALUES ('live', 'skill', 'word', 1)")
    conn.commit()
    report = backfill_session_context(conn)
    rows = {row[0]: row[1] for row in conn.execute("SELECT id, working_directory FROM chat_sessions")}
    assert rows["kept"] == str(root)
    assert rows["gone"] == ""
    assert rows["archived"] == ""
    assert {item["reason"] for item in report["skipped"]} == {"missing_project", "archived_project"}
    again = backfill_session_context(conn)
    assert all(item["idempotent"] for item in again["updated"])
    grant = conn.execute("SELECT resource_id FROM session_resource_grants WHERE session_id = 'kept'").fetchone()
    assert grant[0] == "word"
    conn.close()


@pytest.mark.parametrize("stop_after", ["before_write", "before_fsync", "before_rename", "before_manifest"])
def test_file_stage_resumes_after_each_interrupt(tmp_path: Path, stop_after: str) -> None:
    source = tmp_path / "source.txt"
    source.write_text("payload", encoding="utf-8")
    session_root = tmp_path / "session"
    manifest = tmp_path / "file-manifest.json"
    interrupted = stage_file(source, session_root, "notes/a.txt", manifest, stop_after=stop_after)
    assert interrupted["state"] != "done"
    assert source.read_text(encoding="utf-8") == "payload"
    done = stage_file(source, session_root, "notes/a.txt", manifest)
    assert done["state"] == "done"
    assert done["committed"] is True
    assert (session_root / "notes" / "a.txt").read_text(encoding="utf-8") == "payload"
    assert source.is_file()
    repeated = stage_file(source, session_root, "notes/a.txt", manifest)
    assert repeated["state"] == "done"
    assert list(session_root.rglob("a.txt")) == [session_root / "notes" / "a.txt"]


def test_owner_backfill_reports_orphan_and_unique_job_owner() -> None:
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE scheduled_jobs (
            id TEXT, status TEXT, owner_session_id TEXT DEFAULT '', created_from_session_id TEXT DEFAULT ''
        );
        CREATE TABLE chat_sessions (id TEXT PRIMARY KEY, kind TEXT, title TEXT);
        CREATE TABLE user_notifications (
            id TEXT, owner_session_id TEXT DEFAULT '', session_id TEXT DEFAULT ''
        );
        """
    )
    conn.execute("INSERT INTO scheduled_jobs VALUES ('job-ok', 'active', '', 'ses_1')")
    conn.execute("INSERT INTO scheduled_jobs VALUES ('job-orphan', 'active', '', '')")
    conn.execute("INSERT INTO user_notifications VALUES ('n1', '', 'ses_1')")
    conn.execute("INSERT INTO user_notifications VALUES ('n-orphan', '', '')")
    conn.commit()
    report = backfill_owners(conn)
    owners = {row[0]: row[1] for row in conn.execute("SELECT id, owner_session_id FROM scheduled_jobs")}
    assert owners["job-ok"] == "ses_1"
    assert owners["job-orphan"] == "job-owner-job-orphan"
    assert len(set(owners.values())) == 2
    assert report["activeOwnerCount"] == 2
    assert report["orphans"] == [{"table": "user_notifications", "id": "n-orphan"}]
    assert report["filled"]["user_notifications"] == 1
    kind = conn.execute("SELECT kind FROM chat_sessions WHERE id = 'job-owner-job-orphan'").fetchone()
    assert kind[0] == "job-owner"
    again = backfill_owners(conn)
    assert again["filled"]["scheduled_jobs"] == 0
    conn.close()


def test_cutover_failure_keeps_legacy_read_path(tmp_path: Path) -> None:
    calls = {"paused": 0, "resumed": 0, "switched": 0}

    def pause() -> None:
        calls["paused"] += 1

    def resume() -> None:
        calls["resumed"] += 1

    def switch() -> None:
        calls["switched"] += 1
        raise RuntimeError("switch failed")

    failed = attempt_cutover(
        ready=True,
        pause=pause,
        resume=resume,
        marker=tmp_path / "complete.json",
        switch_reads=switch,
    )
    assert failed["readPath"] == "legacy"
    assert calls["resumed"] == 1
    assert not (tmp_path / "complete.json").exists()
    blocked = attempt_cutover(
        ready=False,
        pause=pause,
        resume=resume,
        marker=tmp_path / "other.json",
        switch_reads=switch,
    )
    assert blocked["cutover"] is False
    assert not (tmp_path / "other.json").exists()


def test_rollback_exports_legacy_copy_without_touching_session(tmp_path: Path) -> None:
    backup = tmp_path / "backup"
    backup.mkdir()
    (backup / "projects.json").write_text("[]", encoding="utf-8")
    session_root = tmp_path / "session"
    session_root.mkdir()
    sentinel = session_root / "keep.txt"
    sentinel.write_text("new", encoding="utf-8")
    flag = {"value": "true"}
    result = rollback_to_legacy(
        backup,
        tmp_path / "export",
        session_root,
        set_legacy_read=lambda: flag.__setitem__("value", "false"),
    )
    assert flag["value"] == "false"
    assert result["sessionUnchanged"] is True
    assert sentinel.read_text(encoding="utf-8") == "new"
    assert (tmp_path / "export" / "projects.json").is_file()
    assert (tmp_path / "export" / "legacy-import.json").is_file()


def test_legacy_cleanup_is_dry_run_and_refuses_unsafe_paths(tmp_path: Path) -> None:
    project = tmp_path / "project"
    hidden = project / ".agent-workstation"
    hidden.mkdir(parents=True)
    internal = hidden / "state.json"
    internal.write_text("{}", encoding="utf-8")
    business = project / "notes.txt"
    business.write_text("keep", encoding="utf-8")
    link = project / ".agents"
    link.symlink_to(tmp_path)
    report = plan_legacy_cleanup(project)
    assert report["dryRun"] is True
    assert internal.is_file()
    assert business.is_file()
    assert str(business) in report["unknown"]
    assert any(item["reason"] == "symlink" for item in report["refused"])
    applied = plan_legacy_cleanup(project, apply=True)
    assert not internal.exists()
    assert business.read_text(encoding="utf-8") == "keep"
    assert applied["deleted"] == [str(internal)]
    assert plan_legacy_cleanup(Path("/"))["refused"] == "unsafe_root"
