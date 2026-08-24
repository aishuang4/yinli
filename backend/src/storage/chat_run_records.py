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
        existing = self.get(record["id"])
        created_at = (
            _pick(record, "createdAt", "created_at")
            or (existing or {}).get("createdAt")
            or now
        )
        updated_at = _pick(record, "updatedAt", "updated_at") or now
        session_id = _pick(record, "sessionId", "session_id")
        if not session_id:
            session_id = (existing or {}).get("sessionId") or ""
        payload = {
            "id": record["id"],
            "sessionId": session_id,
            "workspaceId": _merged(
                record, existing, "workspaceId", "workspace_id", self.default_workspace_id,
            ),
            "projectId": _merged(
                record, existing, "projectId", "project_id", self.default_project_id,
            ),
            "agentId": _merged(record, existing, "agentId", "agent_id", ""),
            "agentName": _merged(record, existing, "agentName", "agent_name", ""),
            "task": _merged(record, existing, "task", "task_text", ""),
            "mode": _merged(record, existing, "mode", "mode", "agent"),
            "status": _merged(record, existing, "status", "status", "running"),
            "triggerSource": _merged(
                record, existing, "triggerSource", "trigger_source", "user",
            ) or "user",
            "scheduledJobId": _merged(
                record, existing, "scheduledJobId", "scheduled_job_id", "",
            ),
            "scheduledFiringId": _merged(
                record, existing, "scheduledFiringId", "scheduled_firing_id", "",
            ),
            "createdAt": created_at,
            "completedAt": _merged(
                record, existing, "completedAt", "completed_at", None, allow_none=True,
            ),
            "updatedAt": updated_at,
        }
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
                    payload["id"], payload["sessionId"],
                    payload["workspaceId"], payload["projectId"],
                    payload["agentId"], payload["agentName"],
                    payload["task"], payload["mode"], payload["status"],
                    payload["triggerSource"], payload["scheduledJobId"],
                    payload["scheduledFiringId"], created_at,
                    payload["completedAt"], updated_at,
                ),
            )
            conn.commit()
        return {**payload, "eventCount": (existing or {}).get("eventCount", 0)}

    def update_status(self, run_id: str, status: str, completed_at: str | None = None) -> None:
        self.ensure_db()
        with self.lock, self.connect() as conn:
            conn.execute(
                "UPDATE chat_runs SET status = ?, completed_at = ?, updated_at = ? WHERE id = ?",
                (status, completed_at, self.utc_now(), run_id),
            )
            conn.commit()

    def list(
        self,
        limit: int = 50,
        *,
        project_id: str | None = None,
        trigger_source: str | None = None,
    ) -> list[dict[str, Any]]:
        self.ensure_db()
        clauses: list[str] = []
        params: list[Any] = []
        if project_id:
            clauses.append("project_id = ?")
            params.append(project_id)
        if trigger_source:
            clauses.append("trigger_source = ?")
            params.append(trigger_source)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with self.lock, self.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM chat_runs {where} ORDER BY updated_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def list_by_statuses(self, statuses: list[str] | tuple[str, ...] | set[str]) -> list[dict[str, Any]]:
        """列出指定状态的全部 Chat Run，reconcile 不得受 list() 的 LIMIT 截断。"""

        wanted = [str(item) for item in statuses if str(item or "").strip()]
        if not wanted:
            return []
        self.ensure_db()
        placeholders = ",".join("?" * len(wanted))
        with self.lock, self.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM chat_runs WHERE status IN ({placeholders}) ORDER BY updated_at DESC",
                wanted,
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


def _pick(record: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in record:
            return record[key]
    return None


def _merged(
    record: dict[str, Any],
    existing: dict[str, Any] | None,
    camel: str,
    snake: str,
    default: Any,
    *,
    allow_none: bool = False,
) -> Any:
    """未传入的列保留旧值，避免部分 upsert 把 trigger_source 打回 user。"""

    if camel in record or snake in record:
        value = record[camel] if camel in record else record[snake]
        if value is None and not allow_none:
            return default
        return value
    if existing is not None:
        return existing.get(camel, default)
    return default
