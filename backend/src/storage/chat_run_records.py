"""Chat Run、原始事件与 RunState 快照的 SQLite 存储。"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any


class ChatRunRecordStore:
    """封装 chat_runs / chat_run_events / chat_run_states 的领域读写。"""

    def __init__(
        self,
        *,
        ensure_db: Callable[[], None],
        connect: Callable[[], sqlite3.Connection],
        lock: Any,
        utc_now: Callable[[], str],
        dumps: Callable[[Any], str],
        loads: Callable[[str | None, Any], Any],
        default_workspace_id: str,
        default_project_id: str,
    ) -> None:
        self.ensure_db = ensure_db
        self.connect = connect
        self.lock = lock
        self.utc_now = utc_now
        self.dumps = dumps
        self.loads = loads
        self.default_workspace_id = default_workspace_id
        self.default_project_id = default_project_id

    def upsert(self, record: dict[str, Any]) -> dict[str, Any]:
        self.ensure_db()
        now = self.utc_now()
        created_at = record.get("createdAt") or record.get("created_at") or now
        updated_at = record.get("updatedAt") or record.get("updated_at") or now
        with self.lock, self.connect() as conn:
            conn.execute(
                """
                INSERT INTO chat_runs (
                    id, session_id, workspace_id, project_id, agent_id, agent_name,
                    task_text, mode, status, created_at, completed_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    session_id=excluded.session_id,
                    workspace_id=excluded.workspace_id,
                    project_id=excluded.project_id,
                    agent_id=excluded.agent_id,
                    agent_name=excluded.agent_name,
                    task_text=excluded.task_text,
                    mode=excluded.mode,
                    status=excluded.status,
                    completed_at=excluded.completed_at,
                    updated_at=excluded.updated_at
                """,
                (
                    record["id"], record["sessionId"],
                    record.get("workspaceId", self.default_workspace_id),
                    record.get("projectId", self.default_project_id),
                    record.get("agentId", ""), record.get("agentName", ""),
                    record.get("task", ""), record.get("mode", "agent"),
                    record.get("status", "running"), created_at,
                    record.get("completedAt"), updated_at,
                ),
            )
            conn.commit()
        return {**record, "createdAt": created_at, "updatedAt": updated_at}

    def update_status(self, run_id: str, status: str, completed_at: str | None = None) -> None:
        self.ensure_db()
        with self.lock, self.connect() as conn:
            conn.execute(
                "UPDATE chat_runs SET status = ?, completed_at = ?, updated_at = ? WHERE id = ?",
                (status, completed_at, self.utc_now(), run_id),
            )
            conn.commit()

    def append_event(self, run_id: str, raw_event: str) -> dict[str, Any]:
        self.ensure_db()
        event_id = f"run-evt-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
        created_at = self.utc_now()
        with self.lock, self.connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(sort_order), -1) + 1 AS next_order FROM chat_run_events WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            sort_order = int(row["next_order"] if row else 0)
            conn.execute(
                "INSERT INTO chat_run_events(id, run_id, raw_event, created_at, sort_order) VALUES (?, ?, ?, ?, ?)",
                (event_id, run_id, raw_event, created_at, sort_order),
            )
            conn.execute("UPDATE chat_runs SET updated_at = ? WHERE id = ?", (created_at, run_id))
            conn.commit()
        return {
            "id": event_id,
            "runId": run_id,
            "rawEvent": raw_event,
            "createdAt": created_at,
            "sortOrder": sort_order,
        }

    def upsert_state(self, run_id: str, state: dict[str, Any]) -> dict[str, Any]:
        self.ensure_db()
        updated_at = self.utc_now()
        payload = {**state, "updatedAt": state.get("updatedAt") or updated_at}
        with self.lock, self.connect() as conn:
            conn.execute(
                """
                INSERT INTO chat_run_states(run_id, state, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    state=excluded.state,
                    updated_at=excluded.updated_at
                """,
                (run_id, self.dumps(payload), updated_at),
            )
            conn.commit()
        return payload

    def get_state(self, run_id: str) -> dict[str, Any] | None:
        self.ensure_db()
        with self.lock, self.connect() as conn:
            row = conn.execute(
                "SELECT state FROM chat_run_states WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        if not row:
            return None
        state = self.loads(row["state"], {})
        return state if isinstance(state, dict) else {}

    def list(
        self,
        limit: int = 50,
        *,
        include_events: bool = True,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        self.ensure_db()
        with self.lock, self.connect() as conn:
            if project_id:
                rows = conn.execute(
                    "SELECT * FROM chat_runs WHERE project_id = ? ORDER BY updated_at DESC LIMIT ?",
                    (project_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM chat_runs ORDER BY updated_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            run_ids = [row["id"] for row in rows]
            if not include_events:
                event_counts = self._event_counts(conn, run_ids)
                return [
                    self._from_row(row, [], event_count=event_counts.get(row["id"], 0))
                    for row in rows
                ]
            events_by_run: dict[str, list[str]] = {run_id: [] for run_id in run_ids}
            if run_ids:
                placeholders = ",".join("?" for _ in run_ids)
                event_rows = conn.execute(
                    f"""
                    SELECT run_id, raw_event FROM chat_run_events
                    WHERE run_id IN ({placeholders})
                    ORDER BY run_id ASC, sort_order ASC, created_at ASC
                    """,
                    run_ids,
                ).fetchall()
                for event_row in event_rows:
                    events_by_run.setdefault(event_row["run_id"], []).append(event_row["raw_event"])
        return [self._from_row(row, events_by_run.get(row["id"], [])) for row in rows]

    def get(self, run_id: str, *, event_limit: int | None = None) -> dict[str, Any] | None:
        self.ensure_db()
        with self.lock, self.connect() as conn:
            row = conn.execute("SELECT * FROM chat_runs WHERE id = ?", (run_id,)).fetchone()
            if not row:
                return None
            count_row = conn.execute(
                "SELECT COUNT(*) AS total FROM chat_run_events WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            event_count = int(count_row["total"] if count_row else 0)
            if event_limit and event_limit > 0:
                event_rows = conn.execute(
                    """
                    SELECT raw_event FROM (
                        SELECT raw_event, sort_order, created_at FROM chat_run_events
                        WHERE run_id = ? ORDER BY sort_order DESC, created_at DESC LIMIT ?
                    ) ORDER BY sort_order ASC, created_at ASC
                    """,
                    (run_id, event_limit),
                ).fetchall()
            else:
                event_rows = conn.execute(
                    """
                    SELECT raw_event FROM chat_run_events
                    WHERE run_id = ? ORDER BY sort_order ASC, created_at ASC
                    """,
                    (run_id,),
                ).fetchall()
        return self._from_row(
            row,
            [event_row["raw_event"] for event_row in event_rows],
            event_count=event_count,
        )

    @staticmethod
    def _event_counts(conn: sqlite3.Connection, run_ids: list[str]) -> dict[str, int]:
        if not run_ids:
            return {}
        placeholders = ",".join("?" for _ in run_ids)
        rows = conn.execute(
            f"""
            SELECT run_id, COUNT(*) AS total FROM chat_run_events
            WHERE run_id IN ({placeholders}) GROUP BY run_id
            """,
            run_ids,
        ).fetchall()
        return {row["run_id"]: int(row["total"]) for row in rows}

    @staticmethod
    def _from_row(
        row: sqlite3.Row,
        events: list[str],
        event_count: int | None = None,
    ) -> dict[str, Any]:
        return {
            "id": row["id"], "sessionId": row["session_id"],
            "workspaceId": row["workspace_id"], "projectId": row["project_id"],
            "agentId": row["agent_id"], "agentName": row["agent_name"],
            "task": row["task_text"], "mode": row["mode"], "status": row["status"],
            "createdAt": row["created_at"], "completedAt": row["completed_at"],
            "updatedAt": row["updated_at"], "events": events,
            "eventCount": len(events) if event_count is None else event_count,
        }
