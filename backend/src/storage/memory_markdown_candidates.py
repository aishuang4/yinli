"""待确认 Markdown 记忆候选的 SQLite 领域存储。

user / global 层记忆直接影响用户所有会话，模型提炼后不再静默写文件，先进入这张表；
Memory 页面采纳后才由 reflection 层真正写入 Markdown。agent 层仍直接写。
"""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Callable
from typing import Any

CANDIDATE_STATUSES = ("pending", "accepted", "rejected", "failed")


class MemoryMarkdownCandidateStore:
    """封装候选的写入、查询与状态流转。"""

    def __init__(
        self,
        *,
        ensure_db: Callable[[], None],
        connect: Callable[[], sqlite3.Connection],
        lock: Any,
        utc_now: Callable[[], str],
    ) -> None:
        self.ensure_db = ensure_db
        self.connect = connect
        self.lock = lock
        self.utc_now = utc_now

    @staticmethod
    def _from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "scope": row["scope"],
            "ownerId": row["owner_id"],
            "section": row["section"],
            "operation": row["operation"],
            "note": row["note"],
            "exactText": row["exact_text"],
            "replacementText": row["replacement_text"],
            "reason": row["reason"],
            "confidence": float(row["confidence"] or 0.0),
            "status": row["status"],
            "resolution": row["resolution"],
            "agentId": row["agent_id"],
            "agentName": row["agent_name"],
            "workspaceId": row["workspace_id"],
            "projectId": row["project_id"],
            "sourceSessionId": row["source_session_id"],
            "sourceRunId": row["source_run_id"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.ensure_db()
        now = self.utc_now()
        record_id = str(payload.get("id") or f"mmc_{uuid.uuid4().hex[:16]}")
        with self.lock, self.connect() as conn:
            conn.execute(
                """
                INSERT INTO memory_markdown_candidates (
                    id, scope, owner_id, section, operation, note, exact_text, replacement_text,
                    reason, confidence, status, resolution, agent_id, agent_name, workspace_id, project_id,
                    source_session_id, source_run_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', '', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    str(payload.get("scope") or "user"),
                    str(payload.get("ownerId") or "default"),
                    str(payload.get("section") or ""),
                    str(payload.get("operation") or "append"),
                    str(payload.get("note") or ""),
                    str(payload.get("exactText") or ""),
                    str(payload.get("replacementText") or ""),
                    str(payload.get("reason") or ""),
                    float(payload.get("confidence") or 0.0),
                    str(payload.get("agentId") or ""),
                    str(payload.get("agentName") or ""),
                    str(payload.get("workspaceId") or "default"),
                    str(payload.get("projectId") or "default"),
                    str(payload.get("sourceSessionId") or ""),
                    str(payload.get("sourceRunId") or ""),
                    now,
                    now,
                ),
            )
            row = conn.execute("SELECT * FROM memory_markdown_candidates WHERE id = ?", (record_id,)).fetchone()
        return self._from_row(row)

    def find_pending_duplicate(self, *, scope: str, owner_id: str, note: str, exact_text: str) -> dict[str, Any] | None:
        """同 scope / owner 下相同内容的待确认候选只留一条。"""

        self.ensure_db()
        with self.lock, self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM memory_markdown_candidates
                WHERE status = 'pending' AND scope = ? AND owner_id = ? AND note = ? AND exact_text = ?
                LIMIT 1
                """,
                (scope, owner_id, note, exact_text),
            ).fetchone()
        return self._from_row(row) if row else None

    def get(self, record_id: str) -> dict[str, Any] | None:
        self.ensure_db()
        with self.lock, self.connect() as conn:
            row = conn.execute("SELECT * FROM memory_markdown_candidates WHERE id = ?", (record_id,)).fetchone()
        return self._from_row(row) if row else None

    def list(
        self,
        *,
        status: str | None = "pending",
        scope: str | None = None,
        project_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        self.ensure_db()
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if scope:
            clauses.append("scope = ?")
            params.append(scope)
        if project_id:
            clauses.append("project_id = ?")
            params.append(project_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.lock, self.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM memory_markdown_candidates {where} ORDER BY created_at DESC LIMIT ?",
                (*params, int(limit)),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def count_pending(self) -> int:
        self.ensure_db()
        with self.lock, self.connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM memory_markdown_candidates WHERE status = 'pending'").fetchone()
        return int(row["n"] if row else 0)

    def set_status(self, record_id: str, *, status: str, resolution: str = "") -> dict[str, Any] | None:
        if status not in CANDIDATE_STATUSES:
            raise ValueError(f"unknown candidate status: {status}")
        self.ensure_db()
        now = self.utc_now()
        with self.lock, self.connect() as conn:
            conn.execute(
                "UPDATE memory_markdown_candidates SET status = ?, resolution = ?, updated_at = ? WHERE id = ?",
                (status, resolution[:500], now, record_id),
            )
            row = conn.execute("SELECT * FROM memory_markdown_candidates WHERE id = ?", (record_id,)).fetchone()
        return self._from_row(row) if row else None
