"""SQLite 执行器。

提供当前租户库的路径、连接、锁、事务和建表。
领域查询在 `records/`。这里不写 profile、Skill 或 MCP 文件。
"""

import json

import logging

import sqlite3

import threading

import time

from collections.abc import Iterator

from contextlib import contextmanager

from datetime import datetime, timezone

from pathlib import Path

from threading import RLock

from pydantic import BaseModel

from typing import Any

from src.config import runtime_paths

from src.config.runtime_paths import (
    INSTALL_BACKEND_ROOT,
    USER_DATA_ROOT,
    data_root,
    ensure_tenant_directories,
)

from src.config.settings import settings

from src.storage.engine.database_schema import BASE_SCHEMA_SQL, META_SCHEMA_SQL, reconcile_columns

from src.storage.engine.sqlite_tx import (
    CHECKPOINT_EVERY,
    WRITE_BUDGET_SECONDS,
    WriteBudget,
    begin_immediate,
    checkpoint_passive,
    commit_write,
    open_autocommit,
    open_reader,
    open_writer,
    rollback_write,
)

from src.storage.engine.permission_schema import PERMISSION_SCHEMA_SQL

from src.storage.config.agent_defaults import (
    is_stale_desktop_default_project_path as _is_stale_desktop_default_project_path,
)

logger = logging.getLogger(__name__)

BACKEND_ROOT = INSTALL_BACKEND_ROOT

DEFAULT_WORKSPACE_ID = "default"

DEFAULT_PROJECT_ID = "default"

PROJECT_RESOURCE_TYPES = ("agent", "skill", "mcp", "http_tool")

def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()

_state_lock = RLock()

_initialized_paths: set[Path] = set()

_path_locks: dict[Path, RLock] = {}

def db_path() -> Path:
    """当前租户的库文件路径；hosted 忽略 database_url，固定在租户目录内。"""

    from src.tenancy.context import current_tenant

    ctx = current_tenant()
    if ctx.kind == "hosted":
        return ctx.data_root / "data" / "state.db"
    configured = _local_db_path()
    if configured.name == "state.db":
        return configured
    return configured.with_name("state.db")

def _local_db_path() -> Path:
    url = settings.database_url
    prefix = "sqlite+aiosqlite:///"
    if url.startswith(prefix):
        return _resolve_sqlite_path(url[len(prefix):])
    if url.startswith("sqlite:///"):
        return _resolve_sqlite_path(url[len("sqlite:///"):])
    return data_root() / "state.db"

def _resolve_sqlite_path(value: str) -> Path:
    """把相对 SQLite 路径固定到 backend 目录，避免启动目录不同造成空库。"""

    path = Path(value)
    if path.is_absolute():
        return path
    return USER_DATA_ROOT / path

def _lock_for(path: Path) -> RLock:
    with _state_lock:
        lock = _path_locks.get(path)
        if lock is None:
            lock = _path_locks[path] = RLock()
        return lock

class _TenantLockProxy:
    """兼容 `with database._lock:` 的旧写法：进入时按当前租户解析到对应路径的 RLock。

    同一个 proxy 可能被多线程同时进入，锁对象放在 threading.local 的栈里，
    保证 __exit__ 释放的是本线程 __enter__ 取到的那一把。
    """

    def __init__(self) -> None:
        self._local = threading.local()

    def __enter__(self) -> RLock:
        stack = getattr(self._local, "stack", None)
        if stack is None:
            stack = self._local.stack = []
        lock = _lock_for(db_path())
        stack.append(lock)
        lock.acquire()
        return lock

    def __exit__(self, *exc) -> bool:
        lock = self._local.stack.pop()
        lock.release()
        return False

_lock = _TenantLockProxy()

def _connect() -> sqlite3.Connection:
    ensure_tenant_directories()
    return open_writer(db_path())

_successful_writes: dict[Path, int] = {}

@contextmanager
def write_tx(*, budget: WriteBudget = "normal") -> Iterator[sqlite3.Connection]:
    """立即写事务。建立在租户 RLock 上，锁内重试 BEGIN IMMEDIATE。"""

    ensure_db()
    deadline = time.monotonic() + WRITE_BUDGET_SECONDS[budget]
    started = time.monotonic()
    with _lock:
        path = db_path()
        conn = open_autocommit(path)
        try:
            begin_immediate(conn, deadline=deadline, started=started)
            try:
                yield conn
                commit_write(conn)
            except Exception:
                rollback_write(conn)
                raise
            _note_successful_write(path, conn)
        finally:
            conn.close()

@contextmanager
def read_conn() -> Iterator[sqlite3.Connection]:
    """只读连接。库还没建好时先走 ensure_db。"""

    ensure_db()
    conn = open_reader(db_path())
    try:
        yield conn
    finally:
        conn.close()

def _note_successful_write(path: Path, conn: sqlite3.Connection) -> None:
    count = _successful_writes.get(path, 0) + 1
    _successful_writes[path] = count
    if count % CHECKPOINT_EVERY != 0:
        return
    checkpoint_passive(conn)
    now = _utc_now()
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            """
            INSERT INTO state_meta (key, value) VALUES ('last_checkpoint_at', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (now,),
        )
        commit_write(conn)
    except Exception:
        rollback_write(conn)
        logger.warning("记录 checkpoint 时间失败", exc_info=True)

def forget_db(path: Path) -> None:
    """删除租户或测试 teardown 时，丢弃该库的初始化状态与锁。"""

    with _state_lock:
        _initialized_paths.discard(path)
        _path_locks.pop(path, None)
        _successful_writes.pop(path, None)

def forget_all() -> None:
    """测试 teardown：清空全部初始化状态与锁。"""

    with _state_lock:
        _initialized_paths.clear()
        _path_locks.clear()
        _successful_writes.clear()
    from src.storage.engine.legacy_archive import forget_archive_checks

    forget_archive_checks()

def _drop_column_if_exists(conn: sqlite3.Connection, table: str, column: str) -> None:
    """删除已下线设计遗留列，避免旧 NOT NULL 约束继续影响当前写入。"""

    columns = {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column in columns:
        conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")

def ensure_db() -> None:
    """Create SQLite tables once per tenant database, safe to call from import-time route loaders."""
    path = db_path()
    if path in _initialized_paths:
        return
    from src.storage.engine.legacy_archive import archive_legacy_layout

    archive_legacy_layout(path.parent)

    with _lock_for(path):
        if path in _initialized_paths:
            return
        with _connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS json_store (
                    namespace TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (namespace, key)
                )
            """)
            conn.executescript(BASE_SCHEMA_SQL)
            conn.executescript(PERMISSION_SCHEMA_SQL)
            conn.executescript(META_SCHEMA_SQL)
            conn.execute("DROP INDEX IF EXISTS idx_group_studios_active_chat")
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_group_studios_active_chat
                    ON group_studios(chat_session_id)
                    WHERE status NOT IN ('done', 'archived', 'failed', 'deleted')
                """
            )
            _drop_column_if_exists(conn, "agents", "callable_agents")
            _drop_column_if_exists(conn, "agents", "max_depth")
            _drop_column_if_exists(conn, "chat_messages", "subagent_calls")
            _drop_column_if_exists(conn, "chat_messages", "team_activities")
            _drop_column_if_exists(conn, "chat_messages", "team_snapshot")
            _drop_column_if_exists(conn, "chat_sessions", "team_id")
            _drop_column_if_exists(conn, "chat_sessions", "team_name")
            _drop_column_if_exists(conn, "plans", "team_id")
            _drop_column_if_exists(conn, "run_events", "team_activity_id")
            conn.execute("DROP TABLE IF EXISTS team_members")
            conn.execute("DROP TABLE IF EXISTS teams")
            conn.execute("DROP TABLE IF EXISTS skills")
            conn.execute("DROP TABLE IF EXISTS workspaces")
            conn.execute("DROP TABLE IF EXISTS project_resource_bindings")
            conn.execute("DROP TABLE IF EXISTS project_job_recipients")
            conn.execute("DELETE FROM json_store WHERE namespace = 'team_templates'")
            conn.execute("DROP TABLE IF EXISTS chat_run_events")
            conn.execute("DROP TABLE IF EXISTS chat_run_states")
            reconcile_columns(conn)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_chat_messages_session_order "
                "ON chat_messages(session_id, sort_order)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_chat_sessions_project_kind "
                "ON chat_sessions(project_slug, kind, updated_at DESC)"
            )
            _drop_column_if_exists(conn, "chat_runs", "team_id")
            _drop_column_if_exists(conn, "chat_runs", "team_name")
            now = _utc_now()
            default_root = runtime_paths.project_default_root(DEFAULT_PROJECT_ID)
            default_root.mkdir(parents=True, exist_ok=True)
            primary = str(default_root.resolve())
            conn.execute(
                """
                INSERT OR IGNORE INTO projects (
                    slug, name, primary_path, status, created_at, updated_at
                ) VALUES (?, ?, ?, 'active', ?, ?)
                """,
                (DEFAULT_PROJECT_ID, "默认项目", primary, now, now),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO project_folders (
                    project_slug, path, access_mode, is_primary, granted_at
                ) VALUES (?, ?, 'readwrite', 1, ?)
                """,
                (DEFAULT_PROJECT_ID, primary, now),
            )
            current = conn.execute(
                "SELECT primary_path FROM projects WHERE slug = ?",
                (DEFAULT_PROJECT_ID,),
            ).fetchone()
            if current is not None and _is_stale_desktop_default_project_path(str(current["primary_path"] or "")):
                conn.execute(
                    "UPDATE projects SET primary_path = ?, updated_at = ? WHERE slug = ?",
                    (primary, now, DEFAULT_PROJECT_ID),
                )
                conn.execute(
                    "UPDATE project_folders SET path = ? WHERE project_slug = ? AND is_primary = 1",
                    (primary, DEFAULT_PROJECT_ID),
                )
            conn.execute("DROP INDEX IF EXISTS idx_memory_nodes_project_date")
            conn.execute("DROP INDEX IF EXISTS idx_trace_observations_project_started")
            conn.executescript("""
                CREATE INDEX IF NOT EXISTS idx_chat_sessions_project_updated
                    ON chat_sessions(project_slug, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_chat_runs_project_updated
                    ON chat_runs(project_slug, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_run_events_project_session_order
                    ON run_events(project_slug, session_id, sort_order, created_at);
                CREATE INDEX IF NOT EXISTS idx_memory_nodes_project_date
                    ON memory_nodes(project_slug, node_date DESC);
                CREATE INDEX IF NOT EXISTS idx_subagent_invocations_project_run
                    ON subagent_invocations(project_slug, run_id, started_at);
                CREATE INDEX IF NOT EXISTS idx_trace_observations_run_started
                    ON trace_observations(run_id, started_at);
                CREATE INDEX IF NOT EXISTS idx_trace_observations_project_started
                    ON trace_observations(project_slug, started_at DESC);
            """)
            conn.execute(
                "INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (1, ?)",
                (now,),
            )
            conn.commit()
            _initialized_paths.add(path)
            from src.tenancy.context import current_tenant

            if not current_tenant().is_hosted:
                violations = conn.execute("PRAGMA foreign_key_check").fetchall()
                if violations:
                    logger.warning("本地库外键检查发现 %s 行不一致", len(violations))

def _json_default(value: Any) -> Any:
    """Pydantic 模型写成字段字典。其余类型仍退回 str，避免整段 JSON 写失败。"""

    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", by_alias=True)
    return str(value)

def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=_json_default)

def _loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default

async def init_db() -> None:
    """Initialize SQLite storage for the current tenant."""
    ensure_db()
    print(f"[DB] SQLite initialized at {db_path()}")

async def close_db() -> None:
    """No persistent process-wide connection is kept."""
    print("[DB] SQLite connections closed")
