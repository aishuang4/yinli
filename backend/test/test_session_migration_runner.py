"""遷移執行入口的七種結果。"""

from __future__ import annotations

import json

import pytest

from src.catalog.paths import workspace_catalog_root
from src.config.runtime_paths import global_agents_root
from src.session_migration.inventory import collect_inventory
from src.session_migration.runner import run_tenant_migration
from src.session_migration.state import cutover_state, migration_dir
from src.sessions.records import create_session
from src.storage import database

pytest_plugins = ["test.fresh_tenant"]


def _wipe_scope() -> None:
    with database._lock, database._connect() as conn:
        conn.execute("DELETE FROM session_resource_grants")
        conn.execute("DELETE FROM project_resource_bindings")
        conn.execute("DELETE FROM scheduled_jobs")
        conn.execute("DELETE FROM chat_sessions")
        conn.execute("DELETE FROM projects")
        conn.execute("DELETE FROM workspaces")
        conn.commit()


def test_fresh_tenant_returns_fresh_cutover(fresh_tenant) -> None:
    result = run_tenant_migration()
    assert result == {"status": "fresh_cutover"}
    assert cutover_state()["cutover"] == "cutover"
    marker = json.loads((migration_dir() / "complete.json").read_text(encoding="utf-8"))
    assert marker == {"ok": True, "fresh": True}


def test_legacy_cutover_matches_inventory(fresh_tenant, tmp_path) -> None:
    root = tmp_path / "legacy-root"
    root.mkdir()
    database.create_workspace({"id": "w-legacy", "name": "歷史"})
    database.create_project({
        "id": "p-legacy",
        "name": "項目",
        "root_path": str(root),
        "workspace_id": "w-legacy",
    })
    session = create_session(title="待回填", agent_id="default")
    with database._lock, database._connect() as conn:
        conn.execute(
            "UPDATE chat_sessions SET project_id = ?, working_directory = '' WHERE id = ?",
            ("p-legacy", session["id"]),
        )
        conn.execute(
            """
            INSERT INTO project_resource_bindings (
                project_id, resource_type, resource_id, enabled, created_at, updated_at
            ) VALUES ('p-legacy', 'mcp', 'srv-1', 1, 't', 't')
            """
        )
        conn.commit()
        inventory = collect_inventory(conn, tenant_root=fresh_tenant)
    try:
        result = run_tenant_migration()
        assert result["status"] == "cutover"
        with database._lock, database._connect() as conn:
            working = conn.execute(
                "SELECT working_directory FROM chat_sessions WHERE id = ?",
                (session["id"],),
            ).fetchone()[0]
            grants = conn.execute(
                "SELECT COUNT(*) FROM session_resource_grants WHERE session_id = ?",
                (session["id"],),
            ).fetchone()[0]
            enabled = conn.execute(
                "SELECT COUNT(*) FROM project_resource_bindings WHERE project_id = 'p-legacy' AND enabled = 1",
            ).fetchone()[0]
        assert working == str(root)
        assert grants == enabled
        assert inventory["counts"]["sessions"] == 1
        again = run_tenant_migration()
        assert again == {"status": "already_cutover"}
        with database._lock, database._connect() as conn:
            working_again = conn.execute(
                "SELECT working_directory FROM chat_sessions WHERE id = ?",
                (session["id"],),
            ).fetchone()[0]
            grants_again = conn.execute(
                "SELECT COUNT(*) FROM session_resource_grants WHERE session_id = ?",
                (session["id"],),
            ).fetchone()[0]
        assert working_again == working
        assert grants_again == grants
    finally:
        _wipe_scope()


def test_blocking_conflict_stays_armed(fresh_tenant) -> None:
    session = create_session(title="缺項目", agent_id="default")
    with database._lock, database._connect() as conn:
        conn.execute(
            "UPDATE chat_sessions SET project_id = 'missing-project' WHERE id = ?",
            (session["id"],),
        )
        conn.commit()
    try:
        result = run_tenant_migration()
        assert result["status"] == "blocked"
        assert cutover_state()["cutover"] == "armed"
    finally:
        _wipe_scope()


def _write_profile(workspace_id: str, body: str) -> None:
    directory = workspace_catalog_root(workspace_id) / "profiles"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "agent-a.md").write_text(body, encoding="utf-8")


def test_same_profile_body_merges(fresh_tenant) -> None:
    body = "---\nname: Agent A\n---\n同一份正文\n"
    database.create_workspace({"id": "w-one", "name": "一"})
    database.create_workspace({"id": "w-two", "name": "二"})
    _write_profile("w-one", body)
    _write_profile("w-two", body)
    try:
        result = run_tenant_migration()
        assert result["status"] == "cutover"
        target = global_agents_root() / "agent-a.md"
        assert target.read_text(encoding="utf-8") == body
    finally:
        _wipe_scope()


def test_different_profile_body_blocks_without_write(fresh_tenant) -> None:
    database.create_workspace({"id": "w-one", "name": "一"})
    database.create_workspace({"id": "w-two", "name": "二"})
    _write_profile("w-one", "正文甲\n")
    _write_profile("w-two", "正文乙\n")
    try:
        result = run_tenant_migration()
        assert result["status"] == "blocked"
        assert not (global_agents_root() / "agent-a.md").exists()
        assert cutover_state()["cutover"] == "armed"
    finally:
        _wipe_scope()


def test_not_ready_when_pending_job_has_no_session(fresh_tenant) -> None:
    database.create_workspace({"id": "w-job", "name": "任務"})
    with database._lock, database._connect() as conn:
        conn.execute(
            """
            INSERT INTO scheduled_jobs (
                id, workspace_id, project_id, name, status, agent_id,
                schedule_kind, schedule_expr, timezone, frozen_prompt,
                created_from_session_id, created_at, updated_at
            ) VALUES (
                'job-pending', 'w-job', '', '待來源', 'pending', 'default',
                'cron', '0 * * * *', 'UTC', 'do', '', 't', 't'
            )
            """
        )
        conn.commit()
    try:
        result = run_tenant_migration()
        assert result["status"] == "not_ready"
        assert result["checks"]["jobs"] is False
        assert cutover_state()["cutover"] == "armed"
    finally:
        _wipe_scope()


def test_dry_run_writes_no_files(fresh_tenant) -> None:
    database.create_workspace({"id": "w-dry", "name": "預演"})
    before = list(migration_dir().rglob("*")) if migration_dir().exists() else []
    try:
        result = run_tenant_migration(dry_run=True)
        assert result["status"] == "dry_run"
        after = list(migration_dir().rglob("*")) if migration_dir().exists() else []
        assert after == before
        assert cutover_state()["cutover"] == "legacy"
    finally:
        _wipe_scope()
