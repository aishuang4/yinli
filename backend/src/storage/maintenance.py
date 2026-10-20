"""租户库备份、恢复与健康检查。

备份只用 sqlite3.Connection.backup()。恢复复制事实源表，重建 schema_version，
不复制运行时账本。
"""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from src.config.runtime_paths import tenant_root
from src.storage.database_schema import (
    BASE_SCHEMA_SQL,
    FACT_SOURCE_TABLES,
    LEDGER_TABLES,
    META_SCHEMA_SQL,
    reconcile_columns,
)
from src.storage.permission_schema import PERMISSION_SCHEMA_SQL
from src.storage.sqlite_tx import (
    StorageLocked,
    begin_immediate,
    open_autocommit,
    open_writer,
    rollback_write,
)

_JSON_STORE_SQL = """
CREATE TABLE IF NOT EXISTS json_store (
    namespace TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (namespace, key)
);
"""


def backup(dest: Path | None = None) -> Path:
    """把当前租户库一致地复制到 backups/state-<ts>.db。"""

    from src.storage import database

    database.ensure_db()
    target = dest or _new_backup_path("state")
    if target.exists():
        raise FileExistsError(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    with database._lock:
        source = open_writer(database.db_path())
        try:
            copied = sqlite3.connect(target)
            try:
                source.backup(copied)
            finally:
                copied.close()
        finally:
            source.close()
    return target


def recover(source: Path, dest: Path | None = None) -> Path:
    """从一份库文件复制事实源到新库。不替换正在使用的库。"""

    origin = Path(source).resolve()
    if not origin.is_file():
        raise FileNotFoundError(origin)
    target = dest or _new_backup_path("state-recovered")
    if target.exists():
        raise FileExistsError(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = open_writer(target)
    try:
        _initialize_schema(conn)
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("ATTACH DATABASE ? AS src", (str(origin),))
        present = {
            str(row["name"])
            for row in conn.execute("SELECT name FROM src.sqlite_master WHERE type = 'table'")
        }
        skipped = set(LEDGER_TABLES)
        for table in FACT_SOURCE_TABLES:
            if table in skipped or table not in present:
                continue
            columns = _shared_columns(conn, table)
            if not columns:
                continue
            listed = ", ".join(columns)
            conn.execute(
                f"INSERT INTO main.{table} ({listed}) SELECT {listed} FROM src.{table}"
            )
        conn.commit()
        conn.execute("DETACH DATABASE src")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("DELETE FROM schema_version")
        conn.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (1, ?)",
            (_utc_now(),),
        )
        conn.commit()
    finally:
        conn.close()
    return target


def health() -> dict[str, object]:
    """报告 WAL 大小、最近一次 checkpoint 和当前写锁。"""

    from src.storage import database

    database.ensure_db()
    path = database.db_path()
    wal = Path(f"{path}-wal")
    with database.read_conn() as conn:
        version = conn.execute("SELECT MAX(version) AS version FROM schema_version").fetchone()
        checkpoint = conn.execute(
            "SELECT value FROM state_meta WHERE key = 'last_checkpoint_at'"
        ).fetchone()
        journal = conn.execute("PRAGMA journal_mode").fetchone()
    return {
        "path": str(path),
        "journalMode": str(journal[0] if journal else ""),
        "walBytes": wal.stat().st_size if wal.exists() else 0,
        "lastCheckpointAt": str(checkpoint["value"]) if checkpoint else "",
        "schemaVersion": int(version["version"] or 0) if version and version["version"] is not None else 0,
        "lock": _probe_lock(path),
    }


def _initialize_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_JSON_STORE_SQL)
    conn.executescript(BASE_SCHEMA_SQL)
    conn.executescript(PERMISSION_SCHEMA_SQL)
    conn.executescript(META_SCHEMA_SQL)
    reconcile_columns(conn)


def _shared_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    main_cols = [str(row["name"]) for row in conn.execute(f"PRAGMA main.table_info({table})")]
    src_cols = {str(row["name"]) for row in conn.execute(f"PRAGMA src.table_info({table})")}
    return [name for name in main_cols if name in src_cols]


def _probe_lock(path: Path) -> str:
    conn = open_autocommit(path)
    started = time.monotonic()
    try:
        begin_immediate(conn, deadline=started + 0.5, started=started)
        rollback_write(conn)
        return "idle"
    except StorageLocked:
        return "busy"
    finally:
        conn.close()


def _new_backup_path(prefix: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return tenant_root() / "backups" / f"{prefix}-{stamp}.db"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
