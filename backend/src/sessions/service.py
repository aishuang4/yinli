"""Session 歸檔、刪除與依賴檢查。"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from typing import Any

from src.config.runtime_paths import session_root
from src.sessions.errors import SessionError
from src.sessions.records import get_session

_RUNNING = {"running", "cancelling"}
_WAITING = {"waiting_user", "waiting_permission", "waiting_external", "waiting"}
_ACTIVE_JOBS = {"active", "pending_confirmation"}
_ACTIVE_STUDIOS = {"open", "paused"}
_HITL_SKILL = {"waiting_user", "waiting"}


def deletion_blockers(session_id: str, conn: Any | None = None) -> dict[str, list[str]]:
    """列出阻止普通刪除的 active Run、HITL、Job 與群聊。"""

    def collect(connection: Any) -> dict[str, list[str]]:
        runs = [
            str(row["id"])
            for row in connection.execute(
                "SELECT id, status FROM chat_runs WHERE session_id = ?",
                (session_id,),
            ).fetchall()
            if str(row["status"] or "") in _RUNNING
        ]
        hitl = [
            str(row["id"])
            for row in connection.execute(
                "SELECT id, status FROM chat_runs WHERE session_id = ?",
                (session_id,),
            ).fetchall()
            if str(row["status"] or "") in _WAITING
        ]
        hitl.extend(
            str(row["id"])
            for row in connection.execute(
                "SELECT id, status FROM skill_runs WHERE session_id = ?",
                (session_id,),
            ).fetchall()
            if str(row["status"] or "") in _HITL_SKILL and str(row["id"]) not in hitl
        )
        jobs = [
            str(row["id"])
            for row in connection.execute(
                """
                SELECT id, status FROM scheduled_jobs
                WHERE owner_session_id = ? OR created_from_session_id = ?
                """,
                (session_id, session_id),
            ).fetchall()
            if str(row["status"] or "") in _ACTIVE_JOBS
        ]
        inflight = [
            str(row["id"])
            for row in connection.execute(
                """
                SELECT DISTINCT j.id AS id
                FROM scheduled_jobs j
                JOIN scheduled_job_runs r ON r.job_id = j.id
                WHERE (j.owner_session_id = ? OR j.created_from_session_id = ?)
                  AND r.status IN ('queued', 'starting', 'running')
                """,
                (session_id, session_id),
            ).fetchall()
        ]
        for job_id in inflight:
            if job_id not in jobs:
                jobs.append(job_id)
        studios = [
            str(row["id"])
            for row in connection.execute(
                """
                SELECT id, status FROM group_studios
                WHERE chat_session_id = ? OR owner_session_id = ?
                """,
                (session_id, session_id),
            ).fetchall()
            if str(row["status"] or "") in _ACTIVE_STUDIOS
        ]
        return {"runs": runs, "hitl": hitl, "jobs": jobs, "studios": studios}

    if conn is not None:
        return collect(conn)
    from src.storage import database

    database.ensure_db()
    with database._lock, database._connect() as connection:
        return collect(connection)


def _raise_if_blocked(blockers: dict[str, list[str]]) -> None:
    if blockers["jobs"]:
        raise SessionError(
            "session_has_active_jobs",
            "Session 仍有 active Job",
            details={"jobs": blockers["jobs"]},
        )
    if blockers["hitl"]:
        raise SessionError(
            "session_has_pending_hitl",
            "Session 仍有待回應的 HITL",
            details={"hitl": blockers["hitl"]},
        )
    if blockers["runs"]:
        raise SessionError(
            "session_has_active_runs",
            "Session 仍有進行中的 Run",
            details={"runs": blockers["runs"]},
        )
    if blockers["studios"]:
        raise SessionError(
            "session_has_active_studios",
            "Session 仍有未結束的群聊",
            details={"studios": blockers["studios"]},
        )


def archive_session(session_id: str) -> dict[str, Any]:
    """歸檔後停止新 Run，保留歷史與外部目錄。發言中的群聊不能歸檔。"""

    from src.storage import database

    database.ensure_db()
    if _studio_is_speaking(session_id):
        raise SessionError(
            "session_has_active_studios",
            "Session 仍有未結束的群聊",
            details={"studios": session_id},
        )
    now = database._utc_now()
    with database._lock, database._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT id, status FROM chat_sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            conn.rollback()
            raise SessionError("session_not_found", "Session 不存在")
        conn.execute(
            """
            UPDATE chat_sessions
            SET status = 'archived', archived_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (now, now, session_id),
        )
        conn.commit()
    session = get_session(session_id)
    if session is None:
        raise SessionError("session_not_found", "Session 不存在")
    return session


def _studio_is_speaking(session_id: str) -> bool:
    """群聊 Run 仍在說話或等審批時，Session 不能歸檔。"""

    from src.storage import database

    with database._lock, database._connect() as conn:
        row = conn.execute(
            """
            SELECT r.id
            FROM group_runs r
            JOIN group_studios s ON s.id = r.group_id
            WHERE (s.chat_session_id = ? OR s.owner_session_id = ?)
              AND r.status IN ('queued', 'running', 'waiting_permission')
            LIMIT 1
            """,
            (session_id, session_id),
        ).fetchone()
    return row is not None


def assert_session_accepts_runs(session_id: str) -> dict[str, Any]:
    """歸檔或缺失的 Session 不能啟動新 Run。"""

    session = get_session(session_id)
    if session is None:
        raise SessionError("session_not_found", "Session 不存在")
    if session["status"] != "active":
        raise SessionError("session_archived", "歸檔 Session 不能啟動新 Run")
    return session


def _restore_deleted_rows(session_row: dict[str, Any], artifact_rows: list[dict[str, Any]]) -> None:
    from src.storage import database

    def insert(conn: Any, table: str, row: dict[str, Any]) -> None:
        columns = list(row)
        placeholders = ", ".join("?" for _ in columns)
        names = ", ".join(columns)
        conn.execute(
            f"INSERT OR REPLACE INTO {table} ({names}) VALUES ({placeholders})",
            [row[column] for column in columns],
        )

    with database._lock, database._connect() as conn:
        insert(conn, "chat_sessions", session_row)
        for row in artifact_rows:
            insert(conn, "session_artifacts", row)
        conn.commit()


def hard_delete_session(
    session_id: str,
    *,
    before_commit: Callable[[Any], None] | None = None,
    fail_after: str = "",
) -> None:
    """只刪除 idle Session 的資料庫歸屬與內部根，保留外部 working directory。"""

    from src.storage import database

    database.ensure_db()
    session = get_session(session_id)
    if session is None:
        raise SessionError("session_not_found", "Session 不存在")
    external = str(session["workingDirectory"] or "")
    from src.sessions.deletion_files import restore_session_files, stage_session_files

    with database._lock, database._connect() as conn:
        external_rows = conn.execute(
            "SELECT path FROM session_artifacts WHERE session_id = ? AND kind = 'external'",
            (session_id,),
        ).fetchall()
    staged = stage_session_files(
        session_id,
        working_directory=external,
        external_paths=[str(row["path"]) for row in external_rows],
    )
    session_row: dict[str, Any] | None = None
    artifact_rows: list[dict[str, Any]] = []
    committed = False
    try:
        with database._lock, database._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if before_commit is not None:
                before_commit(conn)
            blockers = deletion_blockers(session_id, conn)
            if any(blockers.values()):
                conn.rollback()
                _raise_if_blocked(blockers)
            session_row = dict(conn.execute(
                "SELECT * FROM chat_sessions WHERE id = ?",
                (session_id,),
            ).fetchone())
            artifact_rows = [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM session_artifacts WHERE session_id = ?",
                    (session_id,),
                )
            ]
            conn.execute("DELETE FROM session_artifacts WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM chat_sessions WHERE id = ?", (session_id,))
            conn.commit()
            committed = True
        blockers = deletion_blockers(session_id)
        if any(blockers.values()):
            if session_row is not None:
                _restore_deleted_rows(session_row, artifact_rows)
                restore_session_files(session_id)
            committed = False
            _raise_if_blocked(blockers)
        internal = session_root(session_id)
        if internal.exists() and not internal.is_symlink():
            shutil.rmtree(internal)
        elif internal.is_symlink():
            internal.unlink()
        if fail_after == "files":
            raise OSError("injected file failure")
    except SessionError:
        raise
    except Exception:
        if committed and session_row is not None:
            _restore_deleted_rows(session_row, artifact_rows)
            restore_session_files(session_id)
        raise
    _ = staged
