"""SQLite 连接参数、立即写事务与只读连接。

写事务使用 autocommit 连接，再手动 BEGIN IMMEDIATE。这样不会和标准库
在 DML 前自动打开的 DEFERRED 事务叠在一起。
"""

from __future__ import annotations

import random
import sqlite3
import time
from pathlib import Path
from typing import Literal

BUSY_TIMEOUT_MS = 1000
READ_BUSY_TIMEOUT_MS = 5000
JOURNAL_SIZE_LIMIT = 67_108_864
WAL_AUTOCHECKPOINT = 1000
CHECKPOINT_EVERY = 50

WriteBudget = Literal["normal", "transcript", "heartbeat"]
WRITE_BUDGET_SECONDS: dict[WriteBudget, float] = {
    "normal": 20.0,
    "transcript": 60.0,
    "heartbeat": 0.5,
}


class StorageLocked(RuntimeError):
    """写锁超过预算。稳定错误码 persistence_failed:locked。"""

    code = "persistence_failed:locked"


def apply_writer_pragmas(conn: sqlite3.Connection) -> None:
    """写连接的 WAL 参数。只读连接不要调用。"""

    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    conn.execute(f"PRAGMA journal_size_limit={JOURNAL_SIZE_LIMIT}")
    conn.execute(f"PRAGMA wal_autocheckpoint={WAL_AUTOCHECKPOINT}")
    conn.execute("PRAGMA foreign_keys=ON")


def open_writer(path: Path) -> sqlite3.Connection:
    """现有调用方仍用隐式事务和显式 commit。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=BUSY_TIMEOUT_MS / 1000, isolation_level="")
    conn.row_factory = sqlite3.Row
    apply_writer_pragmas(conn)
    return conn


def open_autocommit(path: Path) -> sqlite3.Connection:
    """write_tx 专用。isolation_level=None 后才能手动 BEGIN IMMEDIATE。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None)
    conn.row_factory = sqlite3.Row
    apply_writer_pragmas(conn)
    return conn


def open_reader(path: Path) -> sqlite3.Connection:
    """只读连接。不改 journal_mode，也不做 checkpoint。"""

    uri = f"{path.resolve().as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=READ_BUSY_TIMEOUT_MS / 1000)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={READ_BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def begin_immediate(conn: sqlite3.Connection, *, deadline: float, started: float) -> None:
    """在预算内重试 BEGIN IMMEDIATE。等待超过预算则抛 StorageLocked。"""

    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise StorageLocked("persistence_failed:locked")
        timeout_ms = max(1, min(BUSY_TIMEOUT_MS, int(remaining * 1000)))
        conn.execute(f"PRAGMA busy_timeout={timeout_ms}")
        try:
            conn.execute("BEGIN IMMEDIATE")
            return
        except sqlite3.OperationalError as exc:
            if not _is_locked(exc) or time.monotonic() >= deadline:
                if _is_locked(exc):
                    raise StorageLocked("persistence_failed:locked") from exc
                raise
            _sleep_jitter(deadline, started)


def commit_write(conn: sqlite3.Connection) -> None:
    conn.execute("COMMIT")


def rollback_write(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("ROLLBACK")
    except sqlite3.OperationalError:
        return


def checkpoint_passive(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA wal_checkpoint(PASSIVE)")


def _sleep_jitter(deadline: float, started: float) -> None:
    """前 2 秒用短抖动，之后拉长。不会睡过预算。"""

    elapsed = time.monotonic() - started
    if elapsed < 2:
        delay = random.uniform(0.02, 0.15)
    else:
        delay = random.uniform(0.25, 1.0)
    delay = min(delay, max(0.0, deadline - time.monotonic()))
    if delay > 0:
        time.sleep(delay)


def _is_locked(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "locked" in text or "busy" in text
