"""Chat Trace Observation 的 SQLite 存储。一行一步，按 Run 查询。"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any


OBSERVATION_KINDS = (
    "run",
    "generation",
    "tool",
    "skill",
    "approval",
    "subagent",
    "team-task",
)


class ObservationRecordStore:
    """封装 trace_observations 的开闭与列表。"""

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

    def insert(self, record: dict[str, Any]) -> dict[str, Any]:
        """写入一条未结束或已结束的 Observation。"""

        self.ensure_db()
        payload = self._normalize_insert(record)
        with self.lock, self.connect() as conn:
            conn.execute(
                """
                INSERT INTO trace_observations (
                    id, run_id, session_id, workspace_id, project_id, kind, name,
                    parent_id, status, started_at, ended_at, duration_ms,
                    input_json, output_json, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["id"],
                    payload["runId"],
                    payload["sessionId"],
                    payload["workspaceId"],
                    payload["projectId"],
                    payload["kind"],
                    payload["name"],
                    payload["parentId"],
                    payload["status"],
                    payload["startedAt"],
                    payload["endedAt"],
                    payload["durationMs"],
                    self.dumps(payload["input"]),
                    self.dumps(payload["output"]),
                    self.dumps(payload["metadata"]),
                ),
            )
            conn.commit()
        return payload

    def close(
        self,
        observation_id: str,
        *,
        status: str,
        output: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        ended_at: str | None = None,
    ) -> dict[str, Any] | None:
        """关闭一条 Observation，计算 durationMs。"""

        self.ensure_db()
        current = self.get(observation_id)
        if current is None:
            return None
        if current.get("endedAt"):
            return current
        closed_at = ended_at or self.utc_now()
        duration_ms = _duration_ms(str(current.get("startedAt") or ""), closed_at)
        next_output = output if output is not None else current.get("output") or {}
        next_meta = {**(current.get("metadata") or {}), **(metadata or {})}
        with self.lock, self.connect() as conn:
            conn.execute(
                """
                UPDATE trace_observations
                SET status = ?, ended_at = ?, duration_ms = ?, output_json = ?, metadata_json = ?
                WHERE id = ?
                """,
                (
                    status,
                    closed_at,
                    duration_ms,
                    self.dumps(next_output),
                    self.dumps(next_meta),
                    observation_id,
                ),
            )
            conn.commit()
        return self.get(observation_id)

    def update_fields(
        self,
        observation_id: str,
        *,
        input_data: dict[str, Any] | None = None,
        output: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        name: str | None = None,
    ) -> dict[str, Any] | None:
        """更新未关闭步骤的 input / output / metadata。"""

        self.ensure_db()
        current = self.get(observation_id)
        if current is None:
            return None
        next_input = input_data if input_data is not None else current.get("input") or {}
        next_output = output if output is not None else current.get("output") or {}
        next_meta = {**(current.get("metadata") or {}), **(metadata or {})}
        next_name = name if name is not None else current.get("name") or ""
        with self.lock, self.connect() as conn:
            conn.execute(
                """
                UPDATE trace_observations
                SET name = ?, input_json = ?, output_json = ?, metadata_json = ?
                WHERE id = ?
                """,
                (
                    next_name,
                    self.dumps(next_input),
                    self.dumps(next_output),
                    self.dumps(next_meta),
                    observation_id,
                ),
            )
            conn.commit()
        return self.get(observation_id)

    def get(self, observation_id: str) -> dict[str, Any] | None:
        self.ensure_db()
        with self.lock, self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM trace_observations WHERE id = ?",
                (observation_id,),
            ).fetchone()
        return self._from_row(row) if row else None

    def list_by_run(self, run_id: str) -> list[dict[str, Any]]:
        self.ensure_db()
        with self.lock, self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM trace_observations
                WHERE run_id = ?
                ORDER BY started_at ASC, id ASC
                """,
                (run_id,),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def find_open(
        self,
        *,
        run_id: str,
        kind: str,
        name: str | None = None,
    ) -> dict[str, Any] | None:
        """查找该 Run 下仍未关闭的同类步骤。"""

        self.ensure_db()
        with self.lock, self.connect() as conn:
            if name:
                row = conn.execute(
                    """
                    SELECT * FROM trace_observations
                    WHERE run_id = ? AND kind = ? AND name = ? AND ended_at IS NULL
                    ORDER BY started_at DESC, id DESC
                    LIMIT 1
                    """,
                    (run_id, kind, name),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT * FROM trace_observations
                    WHERE run_id = ? AND kind = ? AND ended_at IS NULL
                    ORDER BY started_at DESC, id DESC
                    LIMIT 1
                    """,
                    (run_id, kind),
                ).fetchone()
        return self._from_row(row) if row else None

    def _normalize_insert(self, record: dict[str, Any]) -> dict[str, Any]:
        started = str(record.get("startedAt") or record.get("started_at") or self.utc_now())
        ended = record.get("endedAt") if "endedAt" in record else record.get("ended_at")
        ended_at = str(ended) if ended else None
        return {
            "id": str(record["id"]),
            "runId": str(record.get("runId") or record.get("run_id") or ""),
            "sessionId": str(record.get("sessionId") or record.get("session_id") or ""),
            "workspaceId": str(
                record.get("workspaceId") or record.get("workspace_id") or self.default_workspace_id
            ),
            "projectId": str(
                record.get("projectId") or record.get("project_id") or self.default_project_id
            ),
            "kind": str(record.get("kind") or ""),
            "name": str(record.get("name") or ""),
            "parentId": record.get("parentId") or record.get("parent_id") or None,
            "status": str(record.get("status") or "running"),
            "startedAt": started,
            "endedAt": ended_at,
            "durationMs": record.get("durationMs") if record.get("durationMs") is not None else (
                _duration_ms(started, ended_at) if ended_at else None
            ),
            "input": record.get("input") if isinstance(record.get("input"), dict) else {},
            "output": record.get("output") if isinstance(record.get("output"), dict) else {},
            "metadata": record.get("metadata") if isinstance(record.get("metadata"), dict) else {},
        }

    def _from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "runId": row["run_id"],
            "sessionId": row["session_id"],
            "workspaceId": row["workspace_id"],
            "projectId": row["project_id"],
            "kind": row["kind"],
            "name": row["name"],
            "parentId": row["parent_id"],
            "status": row["status"],
            "startedAt": row["started_at"],
            "endedAt": row["ended_at"],
            "durationMs": row["duration_ms"],
            "input": self.loads(row["input_json"], {}),
            "output": self.loads(row["output_json"], {}),
            "metadata": self.loads(row["metadata_json"], {}),
        }


def _duration_ms(started_at: str, ended_at: str | None) -> int | None:
    if not started_at or not ended_at:
        return None
    start = _parse_iso(started_at)
    end = _parse_iso(ended_at)
    if start is None or end is None:
        return None
    return max(0, int((end - start).total_seconds() * 1000))


def _parse_iso(value: str):
    from datetime import datetime, timezone

    text = str(value or "").replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed
