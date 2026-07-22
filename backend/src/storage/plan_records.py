"""Plan Mode 的 SQLite 记录存储。"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any


class PlanRecordStore:
    """封装 plans / plan_steps / plan_events 的领域读写。"""

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

    def create(self, plan: dict[str, Any], steps: list[dict[str, Any]]) -> dict[str, Any]:
        self.ensure_db()
        with self.lock, self.connect() as conn:
            conn.execute(
                """
                INSERT INTO plans (
                    id, run_id, session_id, agent_id, title, goal, status,
                    markdown, current_step_id, created_at, updated_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan["id"], plan.get("runId", ""), plan["sessionId"], plan["agentId"],
                    plan["title"], plan["goal"], plan["status"], plan["markdown"],
                    plan.get("currentStepId"), plan["createdAt"], plan["updatedAt"],
                    plan.get("completedAt"),
                ),
            )
            conn.executemany(
                """
                INSERT INTO plan_steps (
                    id, plan_id, step_index, title, description, status,
                    assigned_agent_id, evidence, result, started_at, completed_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [self._step_values(plan["id"], step, require_runtime_fields=True) for step in steps],
            )
            conn.commit()
        return self.get(plan["id"]) or {**plan, "steps": steps}

    def get(self, plan_id: str) -> dict[str, Any] | None:
        self.ensure_db()
        with self.lock, self.connect() as conn:
            plan_row = conn.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
            if not plan_row:
                return None
            step_rows = conn.execute(
                "SELECT * FROM plan_steps WHERE plan_id = ? ORDER BY step_index ASC",
                (plan_id,),
            ).fetchall()
        return self._plan_from_row(plan_row, [self._step_from_row(row) for row in step_rows])

    def list_session(self, session_id: str) -> list[dict[str, Any]]:
        self.ensure_db()
        with self.lock, self.connect() as conn:
            plan_rows = conn.execute(
                "SELECT * FROM plans WHERE session_id = ? ORDER BY created_at ASC",
                (session_id,),
            ).fetchall()
            step_rows = conn.execute(
                """
                SELECT * FROM plan_steps
                WHERE plan_id IN (SELECT id FROM plans WHERE session_id = ?)
                ORDER BY plan_id ASC, step_index ASC
                """,
                (session_id,),
            ).fetchall()
        steps_by_plan: dict[str, list[dict[str, Any]]] = {}
        for row in step_rows:
            steps_by_plan.setdefault(row["plan_id"], []).append(self._step_from_row(row))
        return [self._plan_from_row(row, steps_by_plan.get(row["id"], [])) for row in plan_rows]

    def get_active(
        self,
        session_id: str,
        statuses: list[str] | tuple[str, ...],
    ) -> dict[str, Any] | None:
        if not statuses:
            return None
        self.ensure_db()
        placeholders = ",".join("?" for _ in statuses)
        with self.lock, self.connect() as conn:
            plan_row = conn.execute(
                f"""
                SELECT * FROM plans
                WHERE session_id = ? AND status IN ({placeholders})
                ORDER BY updated_at DESC, created_at DESC
                LIMIT 1
                """,
                (session_id, *statuses),
            ).fetchone()
            if not plan_row:
                return None
            step_rows = conn.execute(
                "SELECT * FROM plan_steps WHERE plan_id = ? ORDER BY step_index ASC",
                (plan_row["id"],),
            ).fetchall()
        return self._plan_from_row(plan_row, [self._step_from_row(row) for row in step_rows])

    def update_plan(
        self,
        plan_id: str,
        *,
        title: str | None = None,
        status: str | None = None,
        markdown: str | None = None,
        current_step_id: str | None = None,
        completed_at: str | None = None,
    ) -> dict[str, Any] | None:
        existing = self.get(plan_id)
        if existing is None:
            return None
        with self.lock, self.connect() as conn:
            conn.execute(
                """
                UPDATE plans
                SET title = ?, status = ?, markdown = ?, current_step_id = ?, completed_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    title if title is not None else existing["title"],
                    status if status is not None else existing["status"],
                    markdown if markdown is not None else existing["markdown"],
                    current_step_id if current_step_id is not None else existing.get("currentStepId"),
                    completed_at if completed_at is not None else existing.get("completedAt"),
                    self.utc_now(),
                    plan_id,
                ),
            )
            conn.commit()
        return self.get(plan_id)

    def update_step(
        self,
        step_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
        status: str | None = None,
        evidence: str | None = None,
        result: str | None = None,
        started_at: str | None = None,
        completed_at: str | None = None,
    ) -> dict[str, Any] | None:
        self.ensure_db()
        with self.lock, self.connect() as conn:
            row = conn.execute("SELECT * FROM plan_steps WHERE id = ?", (step_id,)).fetchone()
            if row is None:
                return None
            conn.execute(
                """
                UPDATE plan_steps
                SET title = ?, description = ?, status = ?, evidence = ?, result = ?, started_at = ?,
                    completed_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    title if title is not None else row["title"],
                    description if description is not None else row["description"],
                    status if status is not None else row["status"],
                    evidence if evidence is not None else row["evidence"],
                    result if result is not None else row["result"],
                    started_at if started_at is not None else row["started_at"],
                    completed_at if completed_at is not None else row["completed_at"],
                    self.utc_now(),
                    step_id,
                ),
            )
            conn.commit()
            next_row = conn.execute("SELECT * FROM plan_steps WHERE id = ?", (step_id,)).fetchone()
        return self._step_from_row(next_row) if next_row else None

    def replace_steps(self, plan_id: str, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
        self.ensure_db()
        now = self.utc_now()
        with self.lock, self.connect() as conn:
            conn.execute("DELETE FROM plan_steps WHERE plan_id = ?", (plan_id,))
            conn.execute(
                "UPDATE plans SET current_step_id = NULL, updated_at = ? WHERE id = ?",
                (now, plan_id),
            )
            conn.executemany(
                """
                INSERT INTO plan_steps (
                    id, plan_id, step_index, title, description, status,
                    assigned_agent_id, evidence, result, started_at, completed_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [self._step_values(plan_id, step, default_updated_at=now) for step in steps],
            )
            conn.commit()
        plan = self.get(plan_id)
        return list((plan or {}).get("steps") or [])

    def append_event(
        self,
        plan_id: str,
        *,
        step_id: str | None,
        event_type: str,
        content: str,
        run_event_id: str | None = None,
    ) -> dict[str, Any]:
        self.ensure_db()
        event_id = f"plan-evt-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
        created_at = self.utc_now()
        with self.lock, self.connect() as conn:
            conn.execute(
                """
                INSERT INTO plan_events(id, plan_id, step_id, run_event_id, event_type, content, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (event_id, plan_id, step_id, run_event_id, event_type, content, created_at),
            )
            conn.commit()
        return {
            "id": event_id,
            "planId": plan_id,
            "stepId": step_id,
            "runEventId": run_event_id,
            "eventType": event_type,
            "content": content,
            "createdAt": created_at,
        }

    @staticmethod
    def _plan_from_row(row: sqlite3.Row, steps: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "id": row["id"], "runId": row["run_id"], "sessionId": row["session_id"],
            "agentId": row["agent_id"], "title": row["title"], "goal": row["goal"],
            "status": row["status"], "markdown": row["markdown"],
            "currentStepId": row["current_step_id"], "steps": steps,
            "createdAt": row["created_at"], "updatedAt": row["updated_at"],
            "completedAt": row["completed_at"],
        }

    @staticmethod
    def _step_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"], "planId": row["plan_id"], "stepIndex": row["step_index"],
            "title": row["title"], "description": row["description"], "status": row["status"],
            "assignedAgentId": row["assigned_agent_id"], "evidence": row["evidence"],
            "result": row["result"], "startedAt": row["started_at"],
            "completedAt": row["completed_at"], "updatedAt": row["updated_at"],
        }

    @staticmethod
    def _step_values(
        plan_id: str,
        step: dict[str, Any],
        *,
        default_updated_at: str = "",
        require_runtime_fields: bool = False,
    ) -> tuple[Any, ...]:
        description = step["description"] if require_runtime_fields else step.get("description", "")
        status = step["status"] if require_runtime_fields else step.get("status", "pending")
        updated_at = (
            step["updatedAt"]
            if require_runtime_fields
            else step.get("updatedAt") or default_updated_at
        )
        return (
            step["id"], plan_id, step["stepIndex"], step["title"],
            description, status,
            step.get("assignedAgentId"), step.get("evidence", ""), step.get("result", ""),
            step.get("startedAt"), step.get("completedAt"), updated_at,
        )
