"""Tenant 只讀 migration inventory。不建立新 schema 事實。"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

_TABLE_QUERIES = {
    "workspaces": "SELECT id, name, status FROM workspaces ORDER BY id",
    "projects": "SELECT id, workspace_id, name, root_path, status FROM projects ORDER BY id",
    "sessions": "SELECT id, title, project_id, workspace_id FROM chat_sessions ORDER BY id",
    "jobs": "SELECT id, name, status, project_id, workspace_id, created_from_session_id FROM scheduled_jobs ORDER BY id",
    "studios": "SELECT id, title, status, project_id, workspace_id, chat_session_id FROM group_studios ORDER BY id",
    "bindings": "SELECT project_id, resource_type, resource_id, enabled FROM project_resource_bindings ORDER BY project_id, resource_type, resource_id",
}
_DEFINITION_DIRS = (
    ("workspace-catalog", "workspaces"),
    ("global-skills", "skills"),
    ("global-agents", "agents"),
    ("global-mcp", "mcp"),
    ("global-tools", "tools"),
    ("global-policies", "policies"),
)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _rows(conn: sqlite3.Connection, table: str, sql: str) -> list[dict[str, Any]]:
    if not _table_exists(conn, table):
        return []
    conn.row_factory = sqlite3.Row
    return [dict(row) for row in conn.execute(sql).fetchall()]


def _files_under(root: Path, kind: str) -> list[dict[str, str]]:
    """只記錄目錄內檔案，不跟隨指向外部的符號連結。"""

    if not root.is_dir() or root.is_symlink():
        return []
    found: list[dict[str, str]] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        found.append({"kind": kind, "sourcePath": str(path)})
    return found


def collect_inventory(conn: sqlite3.Connection, *, tenant_root: Path) -> dict[str, Any]:
    """彙總資料庫列與 catalog / 外部定義檔。呼叫方負責不在此寫入。"""

    workspaces = _rows(conn, "workspaces", _TABLE_QUERIES["workspaces"])
    projects = _rows(conn, "projects", _TABLE_QUERIES["projects"])
    sessions = _rows(conn, "chat_sessions", _TABLE_QUERIES["sessions"])
    jobs = _rows(conn, "scheduled_jobs", _TABLE_QUERIES["jobs"])
    studios = _rows(conn, "group_studios", _TABLE_QUERIES["studios"])
    bindings = _rows(conn, "project_resource_bindings", _TABLE_QUERIES["bindings"])
    definitions: list[dict[str, str]] = []
    for kind, dirname in _DEFINITION_DIRS:
        definitions.extend(_files_under(tenant_root / dirname, kind))
    for project in projects:
        root = Path(str(project.get("root_path") or ""))
        if not str(root):
            continue
        definitions.extend(_files_under(root / ".agents", "project-agents"))
        definitions.extend(_files_under(root / ".agent-workstation", "project-internal"))
    definitions.sort(key=lambda item: (item["kind"], item["sourcePath"]))
    return {
        "workspaces": workspaces,
        "projects": projects,
        "sessions": sessions,
        "jobs": jobs,
        "studios": studios,
        "bindings": bindings,
        "definitions": definitions,
        "counts": {
            "workspaces": len(workspaces),
            "projects": len(projects),
            "sessions": len(sessions),
            "jobs": len(jobs),
            "studios": len(studios),
            "bindings": len(bindings),
            "definitions": len(definitions),
        },
    }
