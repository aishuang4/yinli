"""回填 Trace、通知、Artifact 和 Job 的 owner Session，並報告孤立列。"""

from __future__ import annotations

import sqlite3
from typing import Any

_RULES = (
    ("scheduled_jobs", "owner_session_id", "created_from_session_id", {"active", "pending_confirmation"}),
    ("user_notifications", "owner_session_id", "session_id", set()),
    ("trace_observations", "owner_session_id", "session_id", set()),
    ("session_artifacts", "owner_session_id", "session_id", set()),
    ("memory_nodes", "owner_session_id", "source_session_id", set()),
)
_ACTIVE_JOB = {"active", "pending_confirmation"}


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    if row is None:
        return set()
    return {str(item[1]) for item in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def backfill_owners(conn: sqlite3.Connection) -> dict[str, Any]:
    """有來源欄的列填上唯一 owner。沒有來源的 active 列只進孤立報告。"""

    report: dict[str, Any] = {"filled": {}, "orphans": []}
    for table, owner_column, source_column, active_status in _RULES:
        columns = _columns(conn, table)
        if owner_column not in columns or source_column not in columns:
            continue
        before = conn.total_changes
        conn.execute(
            f"""
            UPDATE {table}
            SET {owner_column} = {source_column}
            WHERE COALESCE({owner_column}, '') = '' AND COALESCE({source_column}, '') != ''
            """
        )
        report["filled"][table] = conn.total_changes - before
        status_sql = ""
        if active_status and "status" in columns:
            marks = ",".join("?" for _ in active_status)
            status_sql = f" AND status IN ({marks})"
            params: tuple[Any, ...] = tuple(sorted(active_status))
        else:
            params = ()
        rows = conn.execute(
            f"SELECT id FROM {table} WHERE COALESCE({owner_column}, '') = ''{status_sql}",
            params,
        ).fetchall()
        for row in rows:
            if table == "scheduled_jobs" and _claim_job_owner(conn, str(row[0])):
                report["filled"][table] = int(report["filled"][table]) + 1
                continue
            report["orphans"].append({"table": table, "id": str(row[0])})
    report["activeOwnerCount"] = _active_owner_count(conn)
    conn.commit()
    return report


def _claim_job_owner(conn: sqlite3.Connection, job_id: str) -> bool:
    """沒有來源的 active Job 建專用 job-owner Session，不和其他 Job 共用。"""

    if "id" not in _columns(conn, "chat_sessions"):
        return False
    session_id = f"job-owner-{job_id}"
    existing = conn.execute("SELECT id FROM chat_sessions WHERE id = ?", (session_id,)).fetchone()
    if existing is None:
        _insert_session(conn, session_id, job_id)
    conn.execute(
        "UPDATE scheduled_jobs SET owner_session_id = ? WHERE id = ? AND COALESCE(owner_session_id, '') = ''",
        (session_id, job_id),
    )
    return True


def _insert_session(conn: sqlite3.Connection, session_id: str, job_id: str) -> None:
    info = conn.execute("PRAGMA table_info(chat_sessions)").fetchall()
    preferred = {
        "id": session_id,
        "title": f"Job {job_id}",
        "agent_id": "job",
        "agent_name": "Job",
        "mode": "agent",
        "kind": "job-owner",
        "status": "active",
        "created_at": "1970-01-01T00:00:00Z",
        "updated_at": "1970-01-01T00:00:00Z",
        "message_count": 0,
        "working_directory": "",
        "directory_grant_version": 0,
        "resource_grant_version": 0,
    }
    columns: list[str] = []
    values: list[Any] = []
    for column in info:
        name = str(column[1])
        not_null = int(column[3]) == 1
        default = column[4]
        if name in preferred:
            columns.append(name)
            values.append(preferred[name])
        elif not_null and default is None and name != "id":
            columns.append(name)
            values.append("")
    if "id" not in columns:
        columns.insert(0, "id")
        values.insert(0, session_id)
    marks = ",".join("?" for _ in columns)
    conn.execute(
        f"INSERT INTO chat_sessions ({','.join(columns)}) VALUES ({marks})",
        values,
    )


def _active_owner_count(conn: sqlite3.Connection) -> int:
    if "owner_session_id" not in _columns(conn, "scheduled_jobs") or "status" not in _columns(conn, "scheduled_jobs"):
        return 0
    marks = ",".join("?" for _ in _ACTIVE_JOB)
    row = conn.execute(
        f"""
        SELECT COUNT(*) FROM scheduled_jobs
        WHERE status IN ({marks}) AND COALESCE(owner_session_id, '') != ''
        """,
        tuple(sorted(_ACTIVE_JOB)),
    ).fetchone()
    return int(row[0]) if row is not None else 0
