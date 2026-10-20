"""Session 聚合根的附加表與原地升級。不回填 Project root。"""

from __future__ import annotations

import sqlite3

SESSION_COLUMN_UPGRADES: tuple[tuple[str, str, str], ...] = (
    ("chat_sessions", "status", "TEXT NOT NULL DEFAULT 'active'"),
    ("chat_sessions", "working_directory", "TEXT NOT NULL DEFAULT ''"),
    ("chat_sessions", "directory_grant_version", "INTEGER NOT NULL DEFAULT 0"),
    ("chat_sessions", "resource_grant_version", "INTEGER NOT NULL DEFAULT 0"),
    ("chat_sessions", "archived_at", "TEXT"),
    ("chat_sessions", "migration_version", "INTEGER NOT NULL DEFAULT 0"),
    ("scheduled_jobs", "owner_session_id", "TEXT NOT NULL DEFAULT ''"),
    ("scheduled_job_runs", "owner_session_id", "TEXT NOT NULL DEFAULT ''"),
    ("user_notifications", "owner_session_id", "TEXT NOT NULL DEFAULT ''"),
    ("skill_evolution_records", "session_id", "TEXT NOT NULL DEFAULT ''"),
    ("memory_nodes", "session_id", "TEXT NOT NULL DEFAULT ''"),
    ("memory_markdown_candidates", "session_id", "TEXT NOT NULL DEFAULT ''"),
    ("group_studios", "owner_session_id", "TEXT NOT NULL DEFAULT ''"),
    ("group_runs", "wave_grant_json", "TEXT NOT NULL DEFAULT ''"),
    ("trace_observations", "owner_session_id", "TEXT NOT NULL DEFAULT ''"),
)

SESSION_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS session_allowed_roots (
    session_id TEXT NOT NULL,
    normalized_path TEXT NOT NULL,
    access_mode TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (session_id, normalized_path),
    FOREIGN KEY(session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_session_allowed_roots_session
    ON session_allowed_roots(session_id, created_at);
CREATE TABLE IF NOT EXISTS session_resource_grants (
    session_id TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    config_override TEXT NOT NULL DEFAULT '{}',
    grant_version INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (session_id, resource_type, resource_id),
    FOREIGN KEY(session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_session_resource_grants_session
    ON session_resource_grants(session_id, resource_type, enabled);
CREATE TABLE IF NOT EXISTS run_context_snapshots (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE,
    session_id TEXT NOT NULL,
    directory_grant_version INTEGER NOT NULL,
    resource_grant_version INTEGER NOT NULL,
    working_directory TEXT NOT NULL DEFAULT '',
    allowed_roots_json TEXT NOT NULL DEFAULT '[]',
    resources_json TEXT NOT NULL DEFAULT '{}',
    model_snapshot_json TEXT NOT NULL DEFAULT '{}',
    approval_snapshot_json TEXT NOT NULL DEFAULT '{}',
    agent_snapshot_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY(session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_run_context_snapshots_session
    ON run_context_snapshots(session_id, created_at);
CREATE TABLE IF NOT EXISTS session_artifacts (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL DEFAULT '',
    owner_session_id TEXT NOT NULL DEFAULT '',
    workspace_id TEXT NOT NULL DEFAULT '',
    project_id TEXT NOT NULL DEFAULT '',
    run_id TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT 'managed',
    path TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_session_artifacts_session
    ON session_artifacts(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_session_artifacts_owner
    ON session_artifacts(owner_session_id, created_at);
"""

_OPTIONAL_INDEXES: tuple[tuple[str, str], ...] = (
    (
        "scheduled_jobs",
        """
        CREATE INDEX IF NOT EXISTS idx_scheduled_jobs_owner_session
            ON scheduled_jobs(owner_session_id, status)
        """,
    ),
    (
        "user_notifications",
        """
        CREATE INDEX IF NOT EXISTS idx_user_notifications_owner_session
            ON user_notifications(owner_session_id, created_at DESC)
        """,
    ),
    (
        "trace_observations",
        """
        CREATE INDEX IF NOT EXISTS idx_trace_observations_session
            ON trace_observations(session_id, started_at DESC)
        """,
    ),
)


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def upgrade_session_schema(conn: sqlite3.Connection) -> None:
    """為舊庫補上 Session grant / snapshot 欄位，不把 Project root 寫進 working_directory。"""

    for table, column, definition in SESSION_COLUMN_UPGRADES:
        if not _table_exists(conn, table):
            continue
        if column not in _column_names(conn, table):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    conn.executescript(SESSION_SCHEMA_SQL)
    for table, sql in _OPTIONAL_INDEXES:
        if _table_exists(conn, table):
            conn.execute(sql)
