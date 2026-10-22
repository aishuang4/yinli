"""托管控制面数据库：账号、会话、租户和平台调用审计。

只在 hosted 下初始化。桌面端没有控制面，调用 ``ensure_control_db`` 会失败。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from threading import RLock

from src.config.runtime_paths import control_root
from src.storage.engine.auth_schema import AUTH_SCHEMA_SQL
from src.tenancy.context import is_hosted_mode

_control_lock = RLock()
_control_ready: Path | None = None

CONTROL_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tenants (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    quota_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT
);
CREATE TABLE IF NOT EXISTS platform_call_audit (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    model_config_id TEXT NOT NULL,
    client_kind TEXT NOT NULL DEFAULT '',
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    status INTEGER NOT NULL DEFAULT 200,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_platform_call_audit_user
    ON platform_call_audit(user_id, created_at DESC);
"""


def control_db_path() -> Path:
    return control_root() / "control.db"


def control_connect() -> sqlite3.Connection:
    """打开控制面连接。WAL、busy_timeout、外键与租户库一致。"""

    path = control_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def ensure_control_db() -> None:
    """建控制面表。desktop 调用直接失败，避免在本机数据目录落下账号库。"""

    if not is_hosted_mode():
        raise RuntimeError("控制面只在托管模式初始化")
    path = control_db_path()
    with _control_lock:
        global _control_ready
        if _control_ready == path:
            return
        with control_connect() as conn:
            conn.executescript(AUTH_SCHEMA_SQL)
            conn.executescript(CONTROL_SCHEMA_SQL)
        _control_ready = path


def forget_control_db() -> None:
    """测试切换数据根时丢掉初始化标记，下次 ensure 会在新路径建库。"""

    global _control_ready
    with _control_lock:
        _control_ready = None
