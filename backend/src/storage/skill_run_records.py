"""Skill 运行状态的 SQLite 领域存储。"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any


class SkillRunRecordStore:
    """封装 Skill Run 的查询与状态写入。"""

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

    def _from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "sessionId": row["session_id"],
            "agentId": row["agent_id"],
            "skillId": row["skill_id"],
            "skillName": row["skill_name"],
            "status": row["status"],
            "currentStep": row["current_step"],
            "collectedInputs": self.loads(row["collected_inputs"], {}),
            "pendingInteractionId": row["pending_interaction_id"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    def get_active(self, session_id: str, agent_id: str) -> dict[str, Any] | None:
        self.ensure_db()
        with self.lock, self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM skill_runs
                WHERE session_id = ? AND agent_id = ?
                  AND status IN ('active', 'waiting_user')
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (session_id, agent_id),
            ).fetchone()
        return self._from_row(row) if row else None

    def get_by_pending_interaction(self, interaction_id: str) -> dict[str, Any] | None:
        self.ensure_db()
        with self.lock, self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM skill_runs
                WHERE pending_interaction_id = ?
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (interaction_id,),
            ).fetchone()
        return self._from_row(row) if row else None

    def upsert(self, record: dict[str, Any]) -> dict[str, Any]:
        self.ensure_db()
        now = self.utc_now()
        run_id = str(record.get("id") or f"skill-run-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}")
        created_at = str(record.get("createdAt") or now)
        payload = {
            "id": run_id,
            "session_id": str(record.get("sessionId") or record.get("session_id") or ""),
            "agent_id": str(record.get("agentId") or record.get("agent_id") or ""),
            "skill_id": str(record.get("skillId") or record.get("skill_id") or ""),
            "skill_name": str(record.get("skillName") or record.get("skill_name") or ""),
            "status": str(record.get("status") or "active"),
            "current_step": str(record.get("currentStep") or record.get("current_step") or ""),
            "collected_inputs": self.dumps(record.get("collectedInputs") or record.get("collected_inputs") or {}),
            "pending_interaction_id": str(
                record.get("pendingInteractionId") or record.get("pending_interaction_id") or "",
            ),
            "created_at": created_at,
            "updated_at": now,
        }
        with self.lock, self.connect() as conn:
            conn.execute(
                """
                INSERT INTO skill_runs (
                    id, session_id, agent_id, skill_id, skill_name, status,
                    current_step, collected_inputs, pending_interaction_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status = excluded.status,
                    current_step = excluded.current_step,
                    collected_inputs = excluded.collected_inputs,
                    pending_interaction_id = excluded.pending_interaction_id,
                    updated_at = excluded.updated_at
                """,
                (
                    payload["id"], payload["session_id"], payload["agent_id"],
                    payload["skill_id"], payload["skill_name"], payload["status"],
                    payload["current_step"], payload["collected_inputs"],
                    payload["pending_interaction_id"], payload["created_at"], payload["updated_at"],
                ),
            )
            conn.commit()
        return self.get_active(payload["session_id"], payload["agent_id"]) or {
            "id": payload["id"],
            "sessionId": payload["session_id"],
            "agentId": payload["agent_id"],
            "skillId": payload["skill_id"],
            "skillName": payload["skill_name"],
            "status": payload["status"],
            "currentStep": payload["current_step"],
            "collectedInputs": self.loads(payload["collected_inputs"], {}),
            "pendingInteractionId": payload["pending_interaction_id"],
            "createdAt": payload["created_at"],
            "updatedAt": payload["updated_at"],
        }
