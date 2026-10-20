"""全局 Session 歸屬的矩陣、遷移護欄、schema 與路徑。"""

from __future__ import annotations

import shutil
import sqlite3
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.config import runtime_paths
from src.config.runtime_paths import SessionPathError
from src.server.main import app
from src.session_migration.backup import write_backup
from src.session_migration.conflicts import precheck
from src.session_migration.inventory import collect_inventory
from src.session_migration.matrix import (
    product_modules,
    scan_references,
    unregistered_references,
    verify_allowlist,
    verify_matrix,
)
from src.session_migration.redaction import contains_plaintext_secret
from src.session_migration.state import cutover_state, migration_dir
from src.sessions.errors import SessionError
from src.sessions.records import (
    add_allowed_root,
    create_session,
    freeze_run_snapshot,
    get_run_snapshot,
    get_session,
    list_allowed_roots,
    list_resource_grants,
    upsert_resource_grant,
)
from src.sessions.schema import upgrade_session_schema
from src.sessions.service import archive_session, assert_session_accepts_runs, hard_delete_session
from src.storage.database import _connect, _lock, ensure_db
from src.tenancy.context import TenantContext, use_tenant


def test_usage_matrix_covers_existing_modules() -> None:
    assert verify_matrix() == []


def test_historical_reference_allowlist_matches_repository() -> None:
    assert verify_allowlist() == []


def test_unregistered_workspace_reference_fails() -> None:
    found = scan_references(runtime_paths.INSTALL_PROJECT_ROOT)
    extra = product_modules(found) | {"backend/src/brand_new_workspace_dependency.py"}
    missing = unregistered_references(extra)
    assert "backend/src/brand_new_workspace_dependency.py" in missing


def _history_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE workspaces (id TEXT PRIMARY KEY, name TEXT, status TEXT);
        CREATE TABLE projects (
            id TEXT PRIMARY KEY, workspace_id TEXT, name TEXT, root_path TEXT, status TEXT
        );
        CREATE TABLE chat_sessions (
            id TEXT PRIMARY KEY, title TEXT, project_id TEXT, workspace_id TEXT
        );
        CREATE TABLE scheduled_jobs (
            id TEXT PRIMARY KEY, name TEXT, status TEXT, project_id TEXT,
            workspace_id TEXT, created_from_session_id TEXT
        );
        CREATE TABLE group_studios (
            id TEXT PRIMARY KEY, title TEXT, status TEXT, project_id TEXT,
            workspace_id TEXT, chat_session_id TEXT
        );
        CREATE TABLE project_resource_bindings (
            project_id TEXT, resource_type TEXT, resource_id TEXT, enabled INTEGER
        );
        """
    )
    return conn


def test_inventory_matches_historical_fixture(tmp_path: Path) -> None:
    tenant = tmp_path / "tenant"
    project = tmp_path / "project"
    (tenant / "workspaces" / "ws" / ".agents" / "profiles").mkdir(parents=True)
    (tenant / "skills" / "word").mkdir(parents=True)
    (project / ".agents" / "mcp").mkdir(parents=True)
    (project / ".agent-workstation" / "jobs").mkdir(parents=True)
    files = [
        tenant / "workspaces" / "ws" / ".agents" / "profiles" / "agent.md",
        tenant / "skills" / "word" / "SKILL.md",
        project / ".agents" / "mcp" / "demo.yaml",
        project / ".agent-workstation" / "jobs" / "daily.md",
    ]
    for path in files:
        path.write_text("name: demo\n", encoding="utf-8")
    conn = _history_db(tmp_path / "history.db")
    conn.execute("INSERT INTO workspaces VALUES ('ws', '工作區', 'active')")
    conn.execute("INSERT INTO projects VALUES ('p', 'ws', '專案', ?, 'active')", (str(project),))
    conn.execute("INSERT INTO chat_sessions VALUES ('s', '對話', 'p', 'ws')")
    conn.execute("INSERT INTO scheduled_jobs VALUES ('j', '日報', 'paused', 'p', 'ws', 's')")
    conn.execute("INSERT INTO group_studios VALUES ('g', '群', 'open', 'p', 'ws', 's')")
    conn.execute("INSERT INTO project_resource_bindings VALUES ('p', 'skill', 'word', 1)")
    conn.commit()
    inventory = collect_inventory(conn, tenant_root=tenant)
    assert inventory["counts"] == {
        "workspaces": 1,
        "projects": 1,
        "sessions": 1,
        "jobs": 1,
        "studios": 1,
        "bindings": 1,
        "definitions": 4,
    }
    assert {item["sourcePath"] for item in inventory["definitions"]} == {str(path) for path in files}
    conn.close()


def test_backup_is_redacted_and_repeatable(tmp_path: Path) -> None:
    tenant = tmp_path / "tenant"
    secret = tenant / "skills" / "demo"
    secret.mkdir(parents=True)
    (secret / "SKILL.md").write_text(
        "name: demo\napiKey: sk-supersecretvalue\nAuthorization: Bearer sk-supersecretvalue\n",
        encoding="utf-8",
    )
    conn = _history_db(tmp_path / "history.db")
    conn.execute(
        "INSERT INTO workspaces VALUES ('ws', '工作區', 'active')"
    )
    conn.commit()
    first = write_backup(conn, tenant_root=tenant, destination=tmp_path / "backup-a")
    second = write_backup(conn, tenant_root=tenant, destination=tmp_path / "backup-b")
    assert first["files"] == second["files"]
    for folder in (tmp_path / "backup-a", tmp_path / "backup-b"):
        for path in folder.rglob("*"):
            if path.is_file():
                assert not contains_plaintext_secret(path.read_text(encoding="utf-8"))
    conn.close()


def test_blocking_conflict_does_not_write_schema(tmp_path: Path) -> None:
    tenant = tmp_path / "tenant"
    left = tenant / "workspaces" / "a" / ".agents" / "profiles"
    right = tenant / "workspaces" / "b" / ".agents" / "profiles"
    left.mkdir(parents=True)
    right.mkdir(parents=True)
    (left / "agent.md").write_text("name: same\nbody: one\n", encoding="utf-8")
    (right / "agent.md").write_text("name: same\nbody: two\n", encoding="utf-8")
    conn = _history_db(tmp_path / "history.db")
    conn.execute("INSERT INTO workspaces VALUES ('a', 'A', 'active')")
    conn.execute("INSERT INTO workspaces VALUES ('b', 'B', 'active')")
    conn.execute("INSERT INTO projects VALUES ('p', 'a', '專案', '/', 'active')")
    conn.execute("INSERT INTO chat_sessions VALUES ('s', '對話', 'missing-project', 'a')")
    conn.execute("INSERT INTO scheduled_jobs VALUES ('j-missing', '缺', 'paused', 'p', 'a', 'gone')")
    conn.execute("INSERT INTO scheduled_jobs VALUES ('j-active', '活', 'active', 'p', 'a', '')")
    conn.commit()
    before = {row[0] for row in conn.execute("SELECT name FROM sqlite_master")}
    result = precheck(conn, tenant_root=tenant)
    after = {row[0] for row in conn.execute("SELECT name FROM sqlite_master")}
    assert before == after
    assert result["writesSchemaFacts"] is False
    assert result["schemaChanged"] is False
    assert result["blocking"] is True
    codes = {item["code"] for item in result["conflicts"]}
    assert {
        "same_id_different_body",
        "path_out_of_bounds",
        "missing_project",
        "missing_session",
        "active_job_without_source",
    } <= codes
    conn.close()


def test_status_api_does_not_start_migration(tmp_path: Path) -> None:
    from src.session_migration.state import set_migration_flag

    set_migration_flag(False, schema_version_value=0)
    ensure_db()
    with _lock, _connect() as conn:
        conn.execute(
            """
            INSERT INTO chat_sessions (id, title, agent_id, agent_name, created_at, updated_at)
            VALUES ('legacy-session', '歷史', 'default', '助手', 't', 't')
            """
        )
        row = conn.execute("SELECT root_path FROM projects LIMIT 1").fetchone()
        project_root = row["root_path"] if row is not None else "/no-historical-project"
        conn.commit()
    session = get_session("legacy-session")
    assert session is not None
    assert session["workingDirectory"] == ""
    assert session["workingDirectory"] != project_root
    state = cutover_state()
    assert state["enabled"] is False
    assert state["schemaVersion"] == 0
    assert state["cutover"] == "legacy"
    assert state["migrationStarted"] is False
    assert not migration_dir().exists()
    client = TestClient(app)
    response = client.get("/api/migrations/global-session-v1")
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is False
    assert body["migrationStarted"] is False
    assert not migration_dir().exists()


def test_old_session_row_upgrade_does_not_invent_project_root(tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "old.db")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE chat_sessions (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            project_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT INTO chat_sessions VALUES ('old', '舊', 'default', 't', 't')"
    )
    conn.execute("CREATE TABLE projects (id TEXT PRIMARY KEY, root_path TEXT)")
    conn.execute("INSERT INTO projects VALUES ('default', '/work/should-not-copy')")
    upgrade_session_schema(conn)
    conn.commit()
    row = conn.execute("SELECT * FROM chat_sessions WHERE id = 'old'").fetchone()
    assert row["working_directory"] == ""
    assert row["status"] == "active"
    assert row["directory_grant_version"] == 0
    assert row["migration_version"] == 0
    conn.close()


def test_allowed_roots_are_unique_and_cascade(tmp_path: Path) -> None:
    session = create_session(session_id="sesroots", working_directory="")
    path = str((tmp_path / "data").resolve())
    add_allowed_root(session["id"], path, access_mode="read_write", source="user")
    with pytest.raises(SessionError) as exc:
        add_allowed_root(session["id"], path, access_mode="read", source="user")
    assert exc.value.code == "duplicate_allowed_root"
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM chat_sessions WHERE id = ?", (session["id"],))
        conn.commit()
    assert list_allowed_roots(session["id"]) == []


def test_resource_grant_version_is_monotonic() -> None:
    session = create_session(session_id="sesgrants")
    errors: list[BaseException] = []

    def write(index: int) -> None:
        try:
            upsert_resource_grant(session["id"], "skill", f"skill-{index}", enabled=True)
        except BaseException as exc:  # noqa: BLE001 - 測試要收集執行緒失敗
            errors.append(exc)

    threads = [threading.Thread(target=write, args=(index,)) for index in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    grants = list_resource_grants(session["id"])
    assert len(grants) == 12
    assert get_session(session["id"])["resourceGrantVersion"] == 12


def test_run_snapshot_ignores_later_grant_changes() -> None:
    session = create_session(session_id="sessnap")
    snapshot = freeze_run_snapshot(
        run_id="run-1",
        session_id=session["id"],
        working_directory="/work/a",
        allowed_roots=[{"normalizedPath": "/work/a", "accessMode": "read_write"}],
        resources={"skills": ["alpha"]},
        model_snapshot={"id": "m1"},
        approval_snapshot={"mode": "manual"},
    )
    upsert_resource_grant(session["id"], "skill", "beta")
    again = get_run_snapshot("run-1")
    assert again == snapshot
    assert again["resources"] == {"skills": ["alpha"]}
    assert again["resourceGrantVersion"] == 0


def test_owner_columns_keep_historical_empty_values() -> None:
    ensure_db()
    with _lock, _connect() as conn:
        conn.execute(
            """
            INSERT INTO scheduled_jobs (
                id, workspace_id, project_id, name, status, agent_id,
                schedule_kind, schedule_expr, timezone, frozen_prompt,
                created_at, updated_at
            ) VALUES ('job-old', 'default', 'default', '舊任務', 'paused', 'default',
                      'daily', '0 9 * * *', 'UTC', 'prompt', 't', 't')
            """
        )
        conn.execute(
            """
            INSERT INTO user_notifications (
                id, workspace_id, project_id, kind, title, created_at
            ) VALUES ('note-old', 'default', 'default', 'job', '標題', 't')
            """
        )
        conn.commit()
        job = conn.execute(
            "SELECT owner_session_id FROM scheduled_jobs WHERE id = 'job-old'"
        ).fetchone()
        note = conn.execute(
            "SELECT owner_session_id FROM user_notifications WHERE id = 'note-old'"
        ).fetchone()
        indexes = {
            str(row["name"])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'").fetchall()
        }
    assert job["owner_session_id"] == ""
    assert note["owner_session_id"] == ""
    assert "idx_scheduled_jobs_owner_session" in indexes
    assert "idx_user_notifications_owner_session" in indexes
    assert "idx_session_artifacts_session" in indexes


def test_delete_rechecks_dependencies_and_keeps_external_directory(tmp_path: Path) -> None:
    external = tmp_path / "external"
    external.mkdir()
    (external / "keep.txt").write_text("user file", encoding="utf-8")
    session = create_session(session_id="sesdel", working_directory=str(external))
    internal = runtime_paths.session_root(session["id"])
    internal.mkdir(parents=True)
    (internal / "state.txt").write_text("internal", encoding="utf-8")
    with _lock, _connect() as conn:
        conn.execute(
            """
            INSERT INTO chat_messages (
                id, session_id, role, content, images, skill_calls, created_at, sort_order
            ) VALUES ('m1', 'sesdel', 'user', 'hi', '[]', '[]', 't', 0)
            """
        )
        conn.commit()

    def appear(conn) -> None:
        conn.execute(
            """
            INSERT INTO chat_runs (id, session_id, status, created_at, updated_at)
            VALUES ('run-live', 'sesdel', 'running', 't', 't')
            """
        )

    with pytest.raises(SessionError) as exc:
        hard_delete_session("sesdel", before_commit=appear)
    assert exc.value.code == "session_has_active_runs"
    assert get_session("sesdel") is not None
    assert (external / "keep.txt").read_text(encoding="utf-8") == "user file"
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM chat_runs WHERE id = 'run-live'")
        conn.commit()
    hard_delete_session("sesdel")
    assert get_session("sesdel") is None
    assert not internal.exists()
    assert (external / "keep.txt").exists()


def test_allowed_roots_do_not_cross_tenants(tmp_path: Path) -> None:
    tenant_a = TenantContext.hosted("usr_" + "aa" * 16)
    tenant_b = TenantContext.hosted("usr_" + "bb" * 16)
    with use_tenant(tenant_a):
        create_session(session_id="sestenant")
        add_allowed_root("sestenant", str(tmp_path / "a"), access_mode="read", source="hosted")
        assert len(list_allowed_roots("sestenant")) == 1
        with pytest.raises(SessionError) as exc:
            add_allowed_root(
                "sestenant",
                str(tenant_b.data_root / "projects"),
                access_mode="read",
                source="hosted",
            )
        assert exc.value.code == "cross_tenant_path"
    with use_tenant(tenant_b):
        assert get_session("sestenant") is None
        assert list_allowed_roots("sestenant") == []
    session = create_session(session_id="sesarch")
    archive_session(session["id"])
    with pytest.raises(SessionError) as exc:
        assert_session_accepts_runs(session["id"])
    assert exc.value.code == "session_archived"
    hard_delete_session(session["id"])
    assert get_session(session["id"]) is None


def test_session_paths_reject_escape_and_cross_tenant(tmp_path: Path) -> None:
    with pytest.raises(SessionPathError) as illegal:
        runtime_paths.session_root("../etc")
    assert illegal.value.code == "illegal_session_id"
    with pytest.raises(SessionPathError):
        runtime_paths.session_root("a/b")

    desktop = runtime_paths.session_root("sesdesk")
    assert desktop.is_relative_to(runtime_paths.tenant_root())
    hosted = TenantContext.hosted("usr_" + "ab" * 16)
    with use_tenant(hosted):
        hosted_path = runtime_paths.session_root("seshost")
    assert hosted_path.is_relative_to(hosted.data_root)
    assert desktop != hosted_path

    outside = tmp_path / "outside-tenant"
    outside.mkdir()
    sessions = runtime_paths.tenant_root() / "sessions"
    if sessions.is_symlink() or sessions.is_file():
        sessions.unlink()
    elif sessions.exists():
        shutil.rmtree(sessions)
    sessions.symlink_to(outside, target_is_directory=True)
    with pytest.raises(SessionPathError) as escaped:
        runtime_paths.session_root("seslink")
    assert escaped.value.code == "cross_tenant"
    sessions.unlink()
    sessions.mkdir()
    (sessions / "seslink").symlink_to(outside, target_is_directory=True)
    with pytest.raises(SessionPathError) as linked:
        runtime_paths.session_root("seslink")
    assert linked.value.code == "symlink_session"
