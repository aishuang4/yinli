"""Chat Run 元数据的 SQLite 存储；展示事件由 Projection Stream 独立持久化。"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any


class ChatRunRecordStore:
    """只封装 chat_runs 元数据，不保存原始 SSE 或 RunState。"""

    def __init__(
        self,
        *,
        ensure_db: Callable[[], None],
        connect: Callable[[], sqlite3.Connection],
        lock: Any,
        utc_now: Callable[[], str],
        default_workspace_id: str,
        default_project_id: str,
    ) -> None:
        self.ensure_db = ensure_db
        self.connect = connect
        self.lock = lock
        self.utc_now = utc_now
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
                    task_text, mode, status, trigger_source, scheduled_job_id,
                    scheduled_firing_id, created_at, completed_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    session_id=excluded.session_id,
                    workspace_id=excluded.workspace_id,
                    project_id=excluded.project_id,
                    agent_id=excluded.agent_id,
                    agent_name=excluded.agent_name,
                    task_text=excluded.task_text,
                    mode=excluded.mode,
                    status=excluded.status,
                    trigger_source=excluded.trigger_source,
                    scheduled_job_id=excluded.scheduled_job_id,
                    scheduled_firing_id=excluded.scheduled_firing_id,
                    completed_at=excluded.completed_at,
                    updated_at=excluded.updated_at
                """,
                (
                    record["id"], record["sessionId"],
                    record.get("workspaceId", self.default_workspace_id),
                    record.get("projectId", self.default_project_id),
                    record.get("agentId", ""), record.get("agentName", ""),
                    record.get("task", ""), record.get("mode", "agent"),
                    record.get("status", "running"),
                    record.get("triggerSource") or record.get("trigger_source") or "user",
                    record.get("scheduledJobId") or record.get("scheduled_job_id") or "",
                    record.get("scheduledFiringId") or record.get("scheduled_firing_id") or "",
                    created_at,
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

    def list(self, limit: int = 50, *, project_id: str | None = None) -> list[dict[str, Any]]:
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
        return [self._from_row(row) for row in rows]

    def get(self, run_id: str) -> dict[str, Any] | None:
        self.ensure_db()
        with self.lock, self.connect() as conn:
            row = conn.execute("SELECT * FROM chat_runs WHERE id = ?", (run_id,)).fetchone()
        return self._from_row(row) if row else None

    @staticmethod
    def _from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "sessionId": row["session_id"],
            "workspaceId": row["workspace_id"],
            "projectId": row["project_id"],
            "agentId": row["agent_id"],
            "agentName": row["agent_name"],
            "task": row["task_text"],
            "mode": row["mode"],
            "status": row["status"],
            "triggerSource": row["trigger_source"] if "trigger_source" in row.keys() else "user",
            "scheduledJobId": row["scheduled_job_id"] if "scheduled_job_id" in row.keys() else "",
            "scheduledFiringId": row["scheduled_firing_id"] if "scheduled_firing_id" in row.keys() else "",
            "createdAt": row["created_at"],
            "completedAt": row["completed_at"],
            "updatedAt": row["updated_at"],
            "eventCount": 0,
        }
