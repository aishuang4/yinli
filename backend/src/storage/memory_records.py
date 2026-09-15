"""长期记忆节点与召回反馈的 SQLite 领域存储。"""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Callable
from typing import Any


class MemoryRecordStore:
    """封装 memory_nodes 与 memory_retrieval_feedback 的读写。"""

    def __init__(self, *, ensure_db: Callable[[], None], connect: Callable[[], sqlite3.Connection],
                 lock: Any, utc_now: Callable[[], str], dumps: Callable[[Any], str],
                 loads: Callable[[str | None, Any], Any], default_workspace_id: str,
                 default_project_id: str) -> None:
        self.ensure_db = ensure_db
        self.connect = connect
        self.lock = lock
        self.utc_now = utc_now
        self.dumps = dumps
        self.loads = loads
        self.default_workspace_id = default_workspace_id
        self.default_project_id = default_project_id

    def upsert(self, data: dict[str, Any]) -> dict[str, Any]:
        self.ensure_db()
        now = self.utc_now()
        created_at = data.get("created_at") or now
        updated_at = data.get("updated_at") or now
        with self.lock, self.connect() as conn:
            conn.execute(
                """
                INSERT INTO memory_nodes (
                    id, workspace_id, project_id, agent_id, user_id, node_date,
                    date_range_start, date_range_end, title, summary, conversation_digest,
                    search_text, user_inputs, agent_final_outputs, tags, source,
                    source_session_id, source_run_id, source_message_id, confidence, status,
                    pinned, vector_status, vector_error, created_at, updated_at,
                    last_used_at, use_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    workspace_id=excluded.workspace_id, project_id=excluded.project_id,
                    agent_id=excluded.agent_id, user_id=excluded.user_id,
                    node_date=excluded.node_date, date_range_start=excluded.date_range_start,
                    date_range_end=excluded.date_range_end, title=excluded.title,
                    summary=excluded.summary, conversation_digest=excluded.conversation_digest,
                    search_text=excluded.search_text, user_inputs=excluded.user_inputs,
                    agent_final_outputs=excluded.agent_final_outputs, tags=excluded.tags,
                    source=excluded.source, source_session_id=excluded.source_session_id,
                    source_run_id=excluded.source_run_id, source_message_id=excluded.source_message_id,
                    confidence=excluded.confidence, status=excluded.status, pinned=excluded.pinned,
                    vector_status=excluded.vector_status, vector_error=excluded.vector_error,
                    updated_at=excluded.updated_at
                """,
                (
                    data["id"], data.get("workspace_id") or self.default_workspace_id,
                    data.get("project_id") or self.default_project_id, data["agent_id"],
                    data.get("user_id") or "default", data["node_date"],
                    data.get("date_range_start") or data["node_date"],
                    data.get("date_range_end") or data["node_date"],
                    data.get("title") or "未命名记忆节点", data.get("summary") or "",
                    data.get("conversation_digest") or "", data.get("search_text") or "",
                    self.dumps(data.get("user_inputs", [])),
                    self.dumps(data.get("agent_final_outputs", [])), self.dumps(data.get("tags", [])),
                    data.get("source") or "chat_run", data.get("source_session_id") or "",
                    data.get("source_run_id") or "", data.get("source_message_id") or "",
                    float(data.get("confidence", 1)), data.get("status") or "active",
                    1 if data.get("pinned") else 0, data.get("vector_status") or "pending",
                    data.get("vector_error") or "", created_at, updated_at,
                    data.get("last_used_at"), int(data.get("use_count") or 0),
                ),
            )
            row = conn.execute("SELECT * FROM memory_nodes WHERE id = ?", (data["id"],)).fetchone()
            conn.commit()
        return self._node_from_row(row)

    def get(self, node_id: str) -> dict[str, Any] | None:
        self.ensure_db()
        with self.lock, self.connect() as conn:
            row = conn.execute("SELECT * FROM memory_nodes WHERE id = ?", (node_id,)).fetchone()
        return self._node_from_row(row) if row else None

    def list(self, *, workspace_id: str = "default", project_id: str | None = None,
             agent_id: str | None = None, user_id: str | None = None,
             date_from: str | None = None, date_to: str | None = None,
             status: str | None = "active", limit: int = 30) -> list[dict[str, Any]]:
        return self._query_nodes(
            workspace_id=workspace_id, project_id=project_id, agent_id=agent_id,
            user_id=user_id, date_from=date_from, date_to=date_to, status=status,
            query_text="", limit=max(1, min(limit, 500)),
        )

    def search(self, *, workspace_id: str = "default", project_id: str | None = None,
               agent_id: str | None = None, user_id: str | None = None,
               date_from: str | None = None, date_to: str | None = None,
               query_text: str = "", status: str | None = "active",
               limit: int = 10) -> list[dict[str, Any]]:
        return self._query_nodes(
            workspace_id=workspace_id, project_id=project_id, agent_id=agent_id,
            user_id=user_id, date_from=date_from, date_to=date_to, status=status,
            query_text=query_text, limit=max(1, min(limit, 100)),
        )

    def _query_nodes(self, *, workspace_id: str, project_id: str | None,
                     agent_id: str | None, user_id: str | None, date_from: str | None,
                     date_to: str | None, status: str | None, query_text: str,
                     limit: int) -> list[dict[str, Any]]:
        self.ensure_db()
        query = "SELECT * FROM memory_nodes WHERE workspace_id = ? AND project_id = ?"
        params: list[Any] = [workspace_id or self.default_workspace_id,
                             project_id or self.default_project_id]
        for clause, value in (("status", status), ("agent_id", agent_id), ("user_id", user_id)):
            if value:
                query += f" AND {clause} = ?"
                params.append(value)
        if date_from:
            query += " AND node_date >= ?"
            params.append(date_from)
        if date_to:
            query += " AND node_date <= ?"
            params.append(date_to)
        if query_text.strip():
            query += " AND search_text LIKE ?"
            params.append(f"%{query_text.strip()}%")
        query += " ORDER BY pinned DESC, node_date DESC, updated_at DESC LIMIT ?"
        params.append(limit)
        with self.lock, self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._node_from_row(row) for row in rows]

    def update_vector_state(self, node_id: str, *, vector_status: str,
                            vector_error: str = "") -> dict[str, Any] | None:
        return self._update_node(
            node_id,
            "vector_status = ?, vector_error = ?, updated_at = ?",
            (vector_status, vector_error, self.utc_now()),
        )

    def update_status(self, node_id: str, *, status: str) -> dict[str, Any] | None:
        return self._update_node(node_id, "status = ?, updated_at = ?", (status, self.utc_now()))

    def find_session_day_node(self, *, workspace_id: str, project_id: str, agent_id: str,
                              node_date: str, source_session_id: str) -> dict[str, Any] | None:
        """同一会话、同一天、同一 Agent 的历史记忆聚合成一个节点。"""

        if not source_session_id:
            return None
        self.ensure_db()
        with self.lock, self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM memory_nodes
                WHERE workspace_id = ? AND project_id = ? AND agent_id = ? AND node_date = ?
                  AND source_session_id = ? AND status = 'active'
                ORDER BY updated_at DESC LIMIT 1
                """,
                (workspace_id or self.default_workspace_id, project_id or self.default_project_id,
                 agent_id, node_date, source_session_id),
            ).fetchone()
        return self._node_from_row(row) if row else None

    def list_stale_active(self, *, before_date: str, unused_since: str, limit: int = 200) -> list[str]:
        """早于 before_date 创建、且 unused_since 之后没被命中的未置顶活跃节点 id。"""

        self.ensure_db()
        with self.lock, self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id FROM memory_nodes
                WHERE status = 'active' AND pinned = 0 AND node_date < ?
                  AND (last_used_at IS NULL OR last_used_at < ?)
                ORDER BY node_date ASC LIMIT ?
                """,
                (before_date, unused_since, int(limit)),
            ).fetchall()
        return [str(row["id"]) for row in rows]

    def mark_used(self, node_id: str) -> dict[str, Any] | None:
        now = self.utc_now()
        return self._update_node(
            node_id,
            "use_count = use_count + 1, last_used_at = ?, updated_at = ?",
            (now, now),
        )

    def _update_node(self, node_id: str, assignments: str,
                     params: tuple[Any, ...]) -> dict[str, Any] | None:
        self.ensure_db()
        with self.lock, self.connect() as conn:
            conn.execute(f"UPDATE memory_nodes SET {assignments} WHERE id = ?", (*params, node_id))
            row = conn.execute("SELECT * FROM memory_nodes WHERE id = ?", (node_id,)).fetchone()
            conn.commit()
        return self._node_from_row(row) if row else None

    def record_feedback(self, data: dict[str, Any]) -> dict[str, Any]:
        self.ensure_db()
        now = self.utc_now()
        feedback_id = data.get("id") or f"mrf_{uuid.uuid4().hex}"
        with self.lock, self.connect() as conn:
            conn.execute(
                """
                INSERT INTO memory_retrieval_feedback (
                    id, audit_event_id, node_id, rating, query, agent_id,
                    session_id, reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (feedback_id, data.get("audit_event_id") or "", data["node_id"],
                 data["rating"], data.get("query") or "", data.get("agent_id") or "",
                 data.get("session_id") or "", data.get("reason") or "",
                 data.get("created_at") or now),
            )
            row = conn.execute(
                "SELECT * FROM memory_retrieval_feedback WHERE id = ?", (feedback_id,),
            ).fetchone()
            conn.commit()
        return self._feedback_from_row(row)

    def list_feedback(self, limit: int = 200) -> list[dict[str, Any]]:
        self.ensure_db()
        safe_limit = max(1, min(int(limit or 200), 1000))
        with self.lock, self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM memory_retrieval_feedback ORDER BY created_at DESC LIMIT ?",
                (safe_limit,),
            ).fetchall()
        return [self._feedback_from_row(row) for row in rows]

    def feedback_scores(self, limit: int = 500) -> dict[str, float]:
        scores: dict[str, float] = {}
        for item in reversed(self.list_feedback(limit)):
            node_id = str(item.get("nodeId") or "")
            rating = str(item.get("rating") or "")
            if not node_id:
                continue
            delta = 0.25 if rating == "relevant" else -0.45 if rating == "irrelevant" else 0.0
            scores[node_id] = max(-1.0, min(scores.get(node_id, 0.0) + delta, 1.0))
        return scores

    def _node_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"], "workspace_id": row["workspace_id"],
            "project_id": row["project_id"], "agent_id": row["agent_id"],
            "user_id": row["user_id"], "node_date": row["node_date"],
            "date_range_start": row["date_range_start"], "date_range_end": row["date_range_end"],
            "title": row["title"], "summary": row["summary"],
            "conversation_digest": row["conversation_digest"], "search_text": row["search_text"],
            "user_inputs": self.loads(row["user_inputs"], []),
            "agent_final_outputs": self.loads(row["agent_final_outputs"], []),
            "tags": self.loads(row["tags"], []), "source": row["source"],
            "source_session_id": row["source_session_id"], "source_run_id": row["source_run_id"],
            "source_message_id": row["source_message_id"],
            "confidence": float(row["confidence"] or 0), "status": row["status"],
            "pinned": bool(row["pinned"]), "vector_status": row["vector_status"],
            "vector_error": row["vector_error"], "created_at": row["created_at"],
            "updated_at": row["updated_at"], "last_used_at": row["last_used_at"],
            "use_count": int(row["use_count"] or 0),
        }

    @staticmethod
    def _feedback_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"], "auditEventId": row["audit_event_id"],
            "nodeId": row["node_id"], "rating": row["rating"], "query": row["query"],
            "agentId": row["agent_id"], "sessionId": row["session_id"],
            "reason": row["reason"], "createdAt": row["created_at"],
        }
