"""把歷史 Session 的 Project root 與 binding 回填成 working directory 和 grant。

歸檔、缺失的 Project 不寫成可用目錄。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    if row is None:
        return set()
    return {str(item[1]) for item in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def backfill_session_context(conn: sqlite3.Connection) -> dict[str, Any]:
    """只在 Project 仍存在且為 active、目錄真實存在時寫入 working directory。"""

    sessions = _columns(conn, "chat_sessions")
    projects = _columns(conn, "projects")
    report: dict[str, Any] = {"updated": [], "skipped": []}
    if "id" not in sessions or "project_id" not in sessions:
        return report
    project_rows: dict[str, sqlite3.Row] = {}
    if projects:
        conn.row_factory = sqlite3.Row
        for row in conn.execute("SELECT * FROM projects").fetchall():
            project_rows[str(row["id"])] = row
    conn.row_factory = sqlite3.Row
    for session in conn.execute("SELECT * FROM chat_sessions").fetchall():
        session_id = str(session["id"])
        project_id = str(session["project_id"] or "")
        project = project_rows.get(project_id)
        status = str(project["status"]) if project is not None and "status" in project.keys() else ""
        root = Path(str(project["root_path"])) if project is not None and "root_path" in project.keys() else Path()
        usable = project is not None and status == "active" and root.is_dir() and not root.is_symlink()
        if not usable:
            reason = "missing_project" if project is None else ("archived_project" if status != "active" else "directory_unavailable")
            report["skipped"].append({"sessionId": session_id, "reason": reason})
            continue
        current = str(session["working_directory"] or "") if "working_directory" in session.keys() else ""
        if current == str(root):
            report["updated"].append({"sessionId": session_id, "workingDirectory": current, "idempotent": True})
        else:
            conn.execute(
                "UPDATE chat_sessions SET working_directory = ?, directory_grant_version = COALESCE(directory_grant_version, 0) + 1 WHERE id = ?",
                (str(root), session_id),
            )
            report["updated"].append({"sessionId": session_id, "workingDirectory": str(root), "idempotent": False})
        _copy_bindings(conn, session_id, project_id)
    conn.commit()
    return report


def _copy_bindings(conn: sqlite3.Connection, session_id: str, project_id: str) -> None:
    if "session_id" not in _columns(conn, "session_resource_grants"):
        return
    if "project_id" not in _columns(conn, "project_resource_bindings"):
        return
    conn.row_factory = sqlite3.Row
    for binding in conn.execute(
        "SELECT resource_type, resource_id, enabled FROM project_resource_bindings WHERE project_id = ? AND enabled = 1",
        (project_id,),
    ).fetchall():
        conn.execute(
            """
            INSERT INTO session_resource_grants (
                session_id, resource_type, resource_id, enabled, config_override,
                grant_version, created_at, updated_at
            ) VALUES (?, ?, ?, 1, '{}', 1, 'migrated', 'migrated')
            ON CONFLICT(session_id, resource_type, resource_id) DO NOTHING
            """,
            (session_id, binding["resource_type"], binding["resource_id"]),
        )


def write_backfill_report(report: dict[str, Any], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
