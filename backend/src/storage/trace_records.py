"""运行轨迹事件与 Subagent Invocation 的 SQLite 领域存储。"""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any


class TraceRecordStore:
    """封装 run_events 与 subagent_invocations 的审计读写。"""

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

    def _event_from_row(self, row: sqlite3.Row, *, include_payload: bool) -> dict[str, Any]:
        return {
            "id": row["id"],
            "sessionId": row["session_id"],
            "workspaceId": row["workspace_id"],
            "projectId": row["project_id"],
            "eventType": row["event_type"],
            "scope": row["scope"],
            "agentId": row["agent_id"],
            "agentName": row["agent_name"],
            "status": row["status"],
            "title": row["title"],
            "content": row["content"],
            "payload": self.loads(row["payload"], {}) if include_payload else {},
            "createdAt": row["created_at"],
            "sortOrder": row["sort_order"],
        }

    def append_event(
        self,
        *,
        session_id: str,
        event_type: str,
        scope: str = "leader",
        agent_id: str | None = None,
        agent_name: str | None = None,
        status: str = "",
        title: str = "",
        content: str = "",
        payload: dict[str, Any] | None = None,
        workspace_id: str | None = None,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        self.ensure_db()
        event_id = f"evt-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
        created_at = self.utc_now()
        with self.lock, self.connect() as conn:
            ownership = conn.execute(
                "SELECT workspace_id, project_id FROM chat_sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            resolved_workspace_id = workspace_id or (
                str(ownership["workspace_id"]) if ownership else self.default_workspace_id
            )
            resolved_project_id = project_id or (
                str(ownership["project_id"]) if ownership else self.default_project_id
            )
            row = conn.execute(
                "SELECT COALESCE(MAX(sort_order), -1) + 1 AS next_order FROM run_events WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            sort_order = int(row["next_order"] if row else 0)
            conn.execute(
                """
                INSERT INTO run_events (
                    id, session_id, workspace_id, project_id, event_type, scope, agent_id, agent_name,
                    status, title, content, payload, created_at, sort_order
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id, session_id, resolved_workspace_id, resolved_project_id, event_type,
                    scope, agent_id, agent_name, status, title, content, self.dumps(payload or {}),
                    created_at, sort_order,
                ),
            )
            conn.commit()
        return {
            "id": event_id,
            "sessionId": session_id,
            "workspaceId": resolved_workspace_id,
            "projectId": resolved_project_id,
            "eventType": event_type,
            "scope": scope,
            "agentId": agent_id,
            "agentName": agent_name,
            "status": status,
            "title": title,
            "content": content,
            "payload": payload or {},
            "createdAt": created_at,
            "sortOrder": sort_order,
        }

    def list_events(
        self,
        session_id: str | None = None,
        *,
        include_payload: bool = True,
        limit: int | None = None,
        offset: int = 0,
        latest: bool = False,
    ) -> list[dict[str, Any]]:
        self.ensure_db()
        safe_offset = max(0, int(offset or 0))
        with self.lock, self.connect() as conn:
            if session_id:
                if limit and limit > 0 and latest:
                    rows = conn.execute(
                        """
                        SELECT * FROM (
                            SELECT * FROM run_events
                            WHERE session_id = ?
                            ORDER BY sort_order DESC, created_at DESC
                            LIMIT ? OFFSET ?
                        ) ORDER BY sort_order ASC, created_at ASC
                        """,
                        (session_id, limit, safe_offset),
                    ).fetchall()
                elif limit and limit > 0:
                    rows = conn.execute(
                        """
                        SELECT * FROM run_events WHERE session_id = ?
                        ORDER BY sort_order ASC, created_at ASC LIMIT ? OFFSET ?
                        """,
                        (session_id, limit, safe_offset),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM run_events WHERE session_id = ? ORDER BY sort_order ASC, created_at ASC",
                        (session_id,),
                    ).fetchall()
            elif limit and limit > 0:
                rows = conn.execute(
                    "SELECT * FROM run_events ORDER BY created_at DESC, sort_order DESC LIMIT ? OFFSET ?",
                    (limit, safe_offset),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM run_events ORDER BY created_at DESC, sort_order DESC",
                ).fetchall()
        return [self._event_from_row(row, include_payload=include_payload) for row in rows]

    def list_events_by_type(
        self,
        event_type: str,
        *,
        project_id: str | None = None,
        include_payload: bool = True,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        self.ensure_db()
        safe_limit = max(1, min(int(limit or 50), 200))
        safe_offset = max(0, int(offset or 0))
        with self.lock, self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM run_events
                WHERE event_type = ? AND project_id = ?
                ORDER BY created_at DESC, sort_order DESC
                LIMIT ? OFFSET ?
                """,
                (event_type, project_id or self.default_project_id, safe_limit, safe_offset),
            ).fetchall()
        return [self._event_from_row(row, include_payload=include_payload) for row in rows]

    def count_events(self, session_id: str) -> int:
        self.ensure_db()
        with self.lock, self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS total FROM run_events WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return int(row["total"] if row else 0)

    def _invocation_to_api(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["id"], "runId": row["run_id"], "sessionId": row["session_id"],
            "workspaceId": row.get("workspace_id", self.default_workspace_id),
            "projectId": row.get("project_id", self.default_project_id),
            "toolCallId": row["tool_call_id"], "parentScope": row["parent_scope"],
            "parentAgentId": row["parent_agent_id"], "parentAgentName": row["parent_agent_name"],
            "definitionScope": row["definition_scope"], "definitionPath": row["definition_path"],
            "subagentName": row["subagent_name"], "status": row["status"], "task": row["task"],
            "executionMode": row.get("execution_mode", "foreground"),
            "expectedOutput": row["expected_output"], "resultPreview": row["result_preview"],
            "resultRef": row.get("result_ref", ""),
            "deliveryStatus": row.get("delivery_status", "none"),
            "error": row["error"], "startedAt": row["started_at"],
            "completedAt": row["completed_at"], "updatedAt": row["updated_at"],
        }

    def create_invocation(self, data: dict[str, Any]) -> dict[str, Any]:
        self.ensure_db()
        invocation_id = data.get("id") or f"subagent-{uuid.uuid4().hex}"
        now = self.utc_now()
        record = {
            "id": invocation_id,
            "run_id": data.get("run_id") or "",
            "session_id": data.get("session_id") or "",
            "workspace_id": data.get("workspace_id") or self.default_workspace_id,
            "project_id": data.get("project_id") or self.default_project_id,
            "tool_call_id": data.get("tool_call_id") or "",
            "parent_scope": data.get("parent_scope") or "single",
            "parent_agent_id": data.get("parent_agent_id") or "",
            "parent_agent_name": data.get("parent_agent_name") or "",
            "definition_scope": data.get("definition_scope") or "",
            "definition_path": data.get("definition_path") or "",
            "subagent_name": data.get("subagent_name") or "",
            "execution_mode": data.get("execution_mode") or "foreground",
            "status": data.get("status") or "running",
            "task": data.get("task") or "",
            "expected_output": data.get("expected_output") or "",
            "result_preview": data.get("result_preview") or "",
            "result_ref": data.get("result_ref") or "",
            "delivery_status": data.get("delivery_status") or "none",
            "error": data.get("error") or "",
            "started_at": data.get("started_at") or now,
            "completed_at": data.get("completed_at"),
            "updated_at": data.get("updated_at") or now,
        }
        with self.lock, self.connect() as conn:
            conn.execute(
                """
                INSERT INTO subagent_invocations (
                    id, run_id, session_id, workspace_id, project_id, tool_call_id, parent_scope,
                    parent_agent_id, parent_agent_name, definition_scope, definition_path,
                    subagent_name, execution_mode, status, task, expected_output, result_preview,
                    result_ref, delivery_status, error,
                    started_at, completed_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["id"], record["run_id"], record["session_id"],
                    record["workspace_id"], record["project_id"], record["tool_call_id"],
                    record["parent_scope"], record["parent_agent_id"],
                    record["parent_agent_name"], record["definition_scope"],
                    record["definition_path"], record["subagent_name"], record["execution_mode"], record["status"],
                    record["task"], record["expected_output"], record["result_preview"],
                    record["result_ref"], record["delivery_status"], record["error"],
                    record["started_at"], record["completed_at"],
                    record["updated_at"],
                ),
            )
            conn.commit()
        return self._invocation_to_api(record)

    def update_invocation(
        self,
        invocation_id: str,
        *,
        status: str,
        result_preview: str = "",
        error: str = "",
        completed_at: str | None = None,
    ) -> dict[str, Any] | None:
        self.ensure_db()
        now = self.utc_now()
        completed_value = completed_at if completed_at is not None else (
            now if status in {"completed", "failed", "cancelled"} else None
        )
        with self.lock, self.connect() as conn:
            conn.execute(
                """
                UPDATE subagent_invocations
                SET status = ?, result_preview = ?, error = ?,
                    completed_at = COALESCE(?, completed_at), updated_at = ?
                WHERE id = ?
                """,
                (status, result_preview, error, completed_value, now, invocation_id),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM subagent_invocations WHERE id = ?",
                (invocation_id,),
            ).fetchone()
        return self._invocation_to_api(dict(row)) if row else None

    def update_invocation_delivery(
        self,
        invocation_id: str,
        *,
        delivery_status: str,
        result_ref: str = "",
    ) -> dict[str, Any] | None:
        """更新可重建文件任务的查询索引，不作为运行时事实源。"""

        self.ensure_db()
        now = self.utc_now()
        with self.lock, self.connect() as conn:
            conn.execute(
                """
                UPDATE subagent_invocations
                SET delivery_status = ?, result_ref = CASE WHEN ? = '' THEN result_ref ELSE ? END,
                    updated_at = ?
                WHERE id = ?
                """,
                (delivery_status, result_ref, result_ref, now, invocation_id),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM subagent_invocations WHERE id = ?",
                (invocation_id,),
            ).fetchone()
        return self._invocation_to_api(dict(row)) if row else None

    def list_invocations(
        self,
        *,
        run_id: str | None = None,
        session_id: str | None = None,
        project_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        self.ensure_db()
        clauses: list[str] = []
        params: list[Any] = []
        if run_id:
            clauses.append("run_id = ?")
            params.append(run_id)
        if session_id:
            clauses.append("session_id = ?")
            params.append(session_id)
        if project_id:
            clauses.append("project_id = ?")
            params.append(project_id)
        query = "SELECT * FROM subagent_invocations"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY started_at DESC LIMIT ?"
        params.append(limit)
        with self.lock, self.connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._invocation_to_api(dict(row)) for row in rows]

    def get_invocation(self, invocation_id: str) -> dict[str, Any] | None:
        """按稳定 invocation id 查询单个 Subagent task。"""

        self.ensure_db()
        with self.lock, self.connect() as conn:
            row = conn.execute("SELECT * FROM subagent_invocations WHERE id = ?", (invocation_id,)).fetchone()
        return self._invocation_to_api(dict(row)) if row else None

    def reconcile_interrupted_invocations(self) -> int:
        """将上一进程遗留的 running task 收口为 interrupted。

        waiting task 不依赖 asyncio.Task，且其 HITL 元数据已经写入 run_events；
        在 checkpoint snapshot 尚未落库前保留 waiting，供后续明确 restart。
        """

        self.ensure_db()
        now = self.utc_now()
        with self.lock, self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE subagent_invocations
                SET status = 'interrupted',
                    error = 'Backend restarted while Subagent was running.',
                    updated_at = ?
                WHERE status IN ('queued', 'running')
                """,
                (now,),
            )
            conn.commit()
        return int(cursor.rowcount)

    def append_transcript_item(
        self,
        *,
        invocation_id: str,
        kind: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """追加一条按 invocation 单调排序的 Subagent transcript。"""

        self.ensure_db()
        item_id = f"subagent-transcript-{uuid.uuid4().hex}"
        created_at = self.utc_now()
        with self.lock, self.connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM subagent_transcript_items WHERE invocation_id = ?",
                (invocation_id,),
            ).fetchone()
            seq = int(row["next_seq"] if row else 1)
            conn.execute(
                """
                INSERT INTO subagent_transcript_items (id, invocation_id, seq, kind, payload, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (item_id, invocation_id, seq, kind, self.dumps(payload), created_at),
            )
            conn.commit()
        return {"id": item_id, "invocationId": invocation_id, "seq": seq, "kind": kind, "payload": payload, "createdAt": created_at}

    def list_transcript_items(self, invocation_id: str, *, limit: int = 500) -> list[dict[str, Any]]:
        """读取 invocation 的 transcript，供后续 detail drawer 和恢复使用。"""

        self.ensure_db()
        with self.lock, self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM subagent_transcript_items WHERE invocation_id = ? ORDER BY seq ASC LIMIT ?",
                (invocation_id, max(1, min(limit, 2_000))),
            ).fetchall()
        return [
            {"id": row["id"], "invocationId": row["invocation_id"], "seq": row["seq"], "kind": row["kind"], "payload": self.loads(row["payload"], {}), "createdAt": row["created_at"]}
            for row in rows
        ]
