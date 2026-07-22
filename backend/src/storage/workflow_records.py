"""Dynamic Workflow Run 的 SQLite 领域存储。"""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Callable
from typing import Any


class WorkflowRecordStore:
    """封装 Workflow Run、节点、边、事件和产物的读写。"""

    def __init__(
        self,
        *,
        ensure_db: Callable[[], None],
        connect: Callable[[], sqlite3.Connection],
        lock: Any,
        utc_now: Callable[[], str],
        dumps: Callable[[Any], str],
        loads: Callable[[str | None, Any], Any],
    ) -> None:
        self.ensure_db = ensure_db
        self.connect = connect
        self.lock = lock
        self.utc_now = utc_now
        self.dumps = dumps
        self.loads = loads

    def create(self, record: dict[str, Any]) -> dict[str, Any]:
        self.ensure_db()
        now = self.utc_now()
        run_id = record.get("id") or f"workflow-{uuid.uuid4().hex}"
        nodes = list(record.get("nodes") or [])
        edges = list(record.get("edges") or [])
        with self.lock, self.connect() as conn:
            conn.execute(
                """
                INSERT INTO workflow_runs (
                    id, schema_version, title, goal, status, current_node_id,
                    created_at, updated_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    record.get("schemaVersion") or record.get("schema_version") or "workflow.v1",
                    record.get("title") or "未命名任务流",
                    record.get("goal") or "",
                    record.get("status") or "draft",
                    record.get("currentNodeId") or "",
                    record.get("createdAt") or now,
                    record.get("updatedAt") or now,
                    record.get("completedAt"),
                ),
            )
            node_id_by_key: dict[str, str] = {}
            for index, node in enumerate(nodes):
                node_id = node.get("id") or f"workflow-node-{uuid.uuid4().hex}"
                node_key = node.get("key") or node.get("nodeKey") or node_id
                node_id_by_key[node_key] = node_id
                conn.execute(
                    """
                    INSERT INTO workflow_nodes (
                        id, run_id, node_key, node_type, title, instruction, status,
                        agent_id, team_id, summary, output, error, position, metadata,
                        started_at, completed_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        node_id, run_id, node_key,
                        node.get("type") or node.get("nodeType") or "agent",
                        node.get("title") or node_key, node.get("instruction") or "",
                        node.get("status") or "pending", node.get("agentId") or "",
                        node.get("teamId") or "", node.get("summary") or "",
                        node.get("output") or "", node.get("error") or "",
                        int(node.get("position") if node.get("position") is not None else index),
                        self.dumps(node.get("metadata") or {}), node.get("startedAt"),
                        node.get("completedAt"), node.get("updatedAt") or now,
                    ),
                )
            for edge in edges:
                from_ref = str(edge.get("from") or edge.get("fromNodeId") or "")
                to_ref = str(edge.get("to") or edge.get("toNodeId") or "")
                from_node_id = node_id_by_key.get(from_ref, from_ref)
                to_node_id = node_id_by_key.get(to_ref, to_ref)
                if not from_node_id or not to_node_id:
                    continue
                conn.execute(
                    """
                    INSERT INTO workflow_edges (
                        id, run_id, from_node_id, to_node_id, condition, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        edge.get("id") or f"workflow-edge-{uuid.uuid4().hex}",
                        run_id, from_node_id, to_node_id,
                        edge.get("condition") or "", edge.get("createdAt") or now,
                    ),
                )
            conn.commit()
        return self.get(run_id) or {"id": run_id}

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        self.ensure_db()
        with self.lock, self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM workflow_runs ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            event_counts = self._event_counts(conn, [row["id"] for row in rows])
        return [self._run_from_row(row, event_counts.get(row["id"], 0)) for row in rows]

    def get(self, run_id: str, *, event_limit: int | None = None) -> dict[str, Any] | None:
        self.ensure_db()
        with self.lock, self.connect() as conn:
            run_row = conn.execute("SELECT * FROM workflow_runs WHERE id = ?", (run_id,)).fetchone()
            if not run_row:
                return None
            node_rows = conn.execute(
                "SELECT * FROM workflow_nodes WHERE run_id = ? ORDER BY position ASC, updated_at ASC",
                (run_id,),
            ).fetchall()
            edge_rows = conn.execute(
                "SELECT * FROM workflow_edges WHERE run_id = ? ORDER BY created_at ASC",
                (run_id,),
            ).fetchall()
            artifact_rows = conn.execute(
                "SELECT * FROM workflow_artifacts WHERE run_id = ? ORDER BY created_at ASC",
                (run_id,),
            ).fetchall()
            count_row = conn.execute(
                "SELECT COUNT(*) AS total FROM workflow_events WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            event_count = int(count_row["total"] if count_row else 0)
            if event_limit and event_limit > 0:
                event_rows = conn.execute(
                    """
                    SELECT * FROM (
                        SELECT * FROM workflow_events WHERE run_id = ?
                        ORDER BY sort_order DESC, created_at DESC LIMIT ?
                    ) ORDER BY sort_order ASC, created_at ASC
                    """,
                    (run_id, event_limit),
                ).fetchall()
            else:
                event_rows = conn.execute(
                    "SELECT * FROM workflow_events WHERE run_id = ? ORDER BY sort_order ASC, created_at ASC",
                    (run_id,),
                ).fetchall()
        return {
            **self._run_from_row(run_row, event_count),
            "nodes": [self._node_from_row(row) for row in node_rows],
            "edges": [self._edge_from_row(row) for row in edge_rows],
            "events": [self._event_from_row(row) for row in event_rows],
            "artifacts": [self._artifact_from_row(row) for row in artifact_rows],
        }

    def update_run_status(
        self,
        run_id: str,
        status: str,
        *,
        current_node_id: str | None = None,
        completed_at: str | None = None,
    ) -> None:
        self.ensure_db()
        with self.lock, self.connect() as conn:
            conn.execute(
                """
                UPDATE workflow_runs SET status = ?,
                    current_node_id = COALESCE(?, current_node_id),
                    completed_at = ?, updated_at = ? WHERE id = ?
                """,
                (status, current_node_id, completed_at, self.utc_now(), run_id),
            )
            conn.commit()

    def update_node_status(
        self,
        node_id: str,
        status: str,
        *,
        summary: str | None = None,
        output: str | None = None,
        error: str | None = None,
        started_at: str | None = None,
        completed_at: str | None = None,
    ) -> dict[str, Any] | None:
        self.ensure_db()
        now = self.utc_now()
        with self.lock, self.connect() as conn:
            conn.execute(
                """
                UPDATE workflow_nodes SET status = ?, summary = COALESCE(?, summary),
                    output = COALESCE(?, output), error = COALESCE(?, error),
                    started_at = COALESCE(?, started_at),
                    completed_at = COALESCE(?, completed_at), updated_at = ?
                WHERE id = ?
                """,
                (status, summary, output, error, started_at, completed_at, now, node_id),
            )
            row = conn.execute("SELECT * FROM workflow_nodes WHERE id = ?", (node_id,)).fetchone()
            if row:
                conn.execute(
                    "UPDATE workflow_runs SET current_node_id = ?, updated_at = ? WHERE id = ?",
                    (node_id if status == "running" else "", now, row["run_id"]),
                )
            conn.commit()
        return self._node_from_row(row) if row else None

    def append_event(
        self,
        run_id: str,
        event_type: str,
        *,
        node_id: str = "",
        status: str = "",
        title: str = "",
        content: str = "",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.ensure_db()
        now = self.utc_now()
        event_id = f"workflow-event-{uuid.uuid4().hex}"
        with self.lock, self.connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(sort_order), -1) + 1 AS next_order FROM workflow_events WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            sort_order = int(row["next_order"] if row else 0)
            conn.execute(
                """
                INSERT INTO workflow_events (
                    id, run_id, node_id, event_type, status, title,
                    content, payload, created_at, sort_order
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id, run_id, node_id, event_type, status, title,
                    content, self.dumps(payload or {}), now, sort_order,
                ),
            )
            conn.execute("UPDATE workflow_runs SET updated_at = ? WHERE id = ?", (now, run_id))
            conn.commit()
        return {
            "id": event_id, "runId": run_id, "nodeId": node_id,
            "type": event_type, "status": status, "title": title,
            "content": content, "payload": payload or {}, "createdAt": now,
            "sortOrder": sort_order,
        }

    @staticmethod
    def _event_counts(conn: sqlite3.Connection, run_ids: list[str]) -> dict[str, int]:
        if not run_ids:
            return {}
        placeholders = ",".join("?" for _ in run_ids)
        rows = conn.execute(
            f"""
            SELECT run_id, COUNT(*) AS total FROM workflow_events
            WHERE run_id IN ({placeholders}) GROUP BY run_id
            """,
            run_ids,
        ).fetchall()
        return {row["run_id"]: int(row["total"]) for row in rows}

    @staticmethod
    def _run_from_row(row: sqlite3.Row, event_count: int = 0) -> dict[str, Any]:
        return {
            "id": row["id"], "schemaVersion": row["schema_version"],
            "title": row["title"], "goal": row["goal"], "status": row["status"],
            "currentNodeId": row["current_node_id"], "createdAt": row["created_at"],
            "updatedAt": row["updated_at"], "completedAt": row["completed_at"],
            "eventCount": event_count,
        }

    def _node_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"], "runId": row["run_id"], "key": row["node_key"],
            "type": row["node_type"], "title": row["title"],
            "instruction": row["instruction"], "status": row["status"],
            "agentId": row["agent_id"], "teamId": row["team_id"],
            "summary": row["summary"], "output": row["output"], "error": row["error"],
            "position": int(row["position"] or 0),
            "metadata": self.loads(row["metadata"], {}),
            "startedAt": row["started_at"], "completedAt": row["completed_at"],
            "updatedAt": row["updated_at"],
        }

    @staticmethod
    def _edge_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"], "runId": row["run_id"],
            "fromNodeId": row["from_node_id"], "toNodeId": row["to_node_id"],
            "condition": row["condition"], "createdAt": row["created_at"],
        }

    def _event_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"], "runId": row["run_id"], "nodeId": row["node_id"],
            "type": row["event_type"], "status": row["status"], "title": row["title"],
            "content": row["content"], "payload": self.loads(row["payload"], {}),
            "createdAt": row["created_at"], "sortOrder": int(row["sort_order"] or 0),
        }

    def _artifact_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"], "runId": row["run_id"], "nodeId": row["node_id"],
            "type": row["artifact_type"], "title": row["title"],
            "content": row["content"], "uri": row["uri"],
            "metadata": self.loads(row["metadata"], {}), "createdAt": row["created_at"],
        }
