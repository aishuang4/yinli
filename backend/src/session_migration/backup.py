"""遷移備份。輸出可重複、且不含明文 secret。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from src.session_migration.inventory import collect_inventory
from src.session_migration.redaction import redact_json, redact_text

_TABLES = (
    "workspaces",
    "projects",
    "project_resource_bindings",
    "chat_sessions",
    "scheduled_jobs",
    "group_studios",
)


def _dump_table(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    if row is None:
        return []
    conn.row_factory = sqlite3.Row
    columns = [info[1] for info in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    order = columns[0]
    rows = []
    for record in conn.execute(f"SELECT * FROM {table} ORDER BY {order}").fetchall():
        rows.append(redact_json({column: record[column] for column in columns}))
    return rows


def _write_bytes(path: Path, payload: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def write_backup(conn: sqlite3.Connection, *, tenant_root: Path, destination: Path) -> dict[str, Any]:
    """把相關表、catalog、policy、Job 定義和檔案清單寫進 destination。"""

    destination.mkdir(parents=True, exist_ok=True)
    inventory = collect_inventory(conn, tenant_root=tenant_root)
    entries: list[dict[str, str]] = []
    for table in _TABLES:
        body = json.dumps(_dump_table(conn, table), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        relative = f"tables/{table}.json"
        digest = _write_bytes(destination / relative, body.encode("utf-8"))
        entries.append({"path": relative, "sha256": digest})
    for item in inventory["definitions"]:
        source = Path(item["sourcePath"])
        relative = "files/" + source.name + "-" + hashlib.sha256(str(source).encode("utf-8")).hexdigest()[:12]
        text = redact_text(source.read_text(encoding="utf-8", errors="ignore"))
        if not text.endswith("\n"):
            text += "\n"
        digest = _write_bytes(destination / relative, text.encode("utf-8"))
        entries.append({"path": relative, "sha256": digest, "sourcePath": str(source)})
    manifest_body = {
        "counts": inventory["counts"],
        "files": entries,
    }
    manifest_bytes = (json.dumps(manifest_body, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    (destination / "manifest.json").write_bytes(manifest_bytes)
    return manifest_body
