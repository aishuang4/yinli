"""遷移 feature flag、schema version 與 cutover 狀態。

讀取狀態不會建立遷移目錄，也不會開始回填。
"""

from __future__ import annotations

from typing import Any

from src.config.runtime_paths import tenant_root

NAMESPACE = "system_settings"
FLAG_KEY = "global_session_v1"
SCHEMA_KEY = "global_session_v1_schema_version"
LEGACY_SCHEMA_VERSION = 0
TARGET_SCHEMA_VERSION = 1


def migration_dir():
    """遷移證據目錄。預設讀取不會建立它。"""

    return tenant_root() / "migrations" / "global-session-v1"


def _read_store(key: str) -> str | None:
    from src.storage.database import ensure_db

    ensure_db()
    from src.storage.database import _connect, _lock

    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT value FROM json_store WHERE namespace = ? AND key = ?",
            (NAMESPACE, key),
        ).fetchone()
    if row is None:
        return None
    return str(row["value"])


def migration_enabled() -> bool:
    """缺省 flag 視為關閉。"""

    raw = _read_store(FLAG_KEY)
    return raw == "true"


def schema_version() -> int:
    """未寫入版本時保持 legacy 0，避免隱式進入新 schema 讀路徑。"""

    raw = _read_store(SCHEMA_KEY)
    if raw is None or not str(raw).strip().isdigit():
        return LEGACY_SCHEMA_VERSION
    return int(raw)


def cutover_state() -> dict[str, Any]:
    """給 API 的只讀狀態。"""

    enabled = migration_enabled()
    version = schema_version()
    if not enabled:
        phase = "legacy"
    elif version >= TARGET_SCHEMA_VERSION:
        phase = "cutover"
    else:
        phase = "armed"
    return {
        "enabled": enabled,
        "schemaVersion": version,
        "cutover": phase,
        "migrationStarted": migration_dir().exists(),
        "targetSchemaVersion": TARGET_SCHEMA_VERSION,
    }


def set_migration_flag(enabled: bool, *, schema_version_value: int | None = None) -> None:
    """顯式寫入 flag。狀態 API 不得呼叫本函式。"""

    from src.storage.database import _connect, _lock, _utc_now, ensure_db

    ensure_db()
    now = _utc_now()
    with _lock, _connect() as conn:
        conn.execute(
            """
            INSERT INTO json_store (namespace, key, value, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(namespace, key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (NAMESPACE, FLAG_KEY, "true" if enabled else "false", now, now),
        )
        if schema_version_value is not None:
            conn.execute(
                """
                INSERT INTO json_store (namespace, key, value, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(namespace, key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (NAMESPACE, SCHEMA_KEY, str(schema_version_value), now, now),
            )
        conn.commit()
