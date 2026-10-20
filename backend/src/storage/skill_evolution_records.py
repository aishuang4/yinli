"""Skill 自进化候选的 SQLite 领域存储。"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any


class SkillEvolutionRecordStore:
    """封装 Skill 自进化候选的写入、查询和状态流转。"""

    def __init__(
        self,
        *,
        ensure_db: Callable[[], None],
        connect: Callable[[], sqlite3.Connection],
        lock: Any,
        utc_now: Callable[[], str],
        dumps: Callable[[Any], str],
        loads: Callable[[str | None, Any], Any],
        default_project_id: str,
    ) -> None:
        self.ensure_db = ensure_db
        self.connect = connect
        self.lock = lock
        self.utc_now = utc_now
        self.dumps = dumps
        self.loads = loads
        self.default_project_id = default_project_id

    def _from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "workspaceId": row["workspace_id"],
            "projectId": row["project_id"],
            "agentId": row["agent_id"],
            "agentName": row["agent_name"],
            "teamId": row["team_id"],
            "userId": row["user_id"],
            "sourceSessionId": row["source_session_id"],
            "sourceRunId": row["source_run_id"],
            "sourceMessageId": row["source_message_id"],
            "routerGate": self.loads(row["router_gate"], {}),
            "workflowEvidence": self.loads(row["workflow_evidence"], {}),
            "reusable": bool(row["reusable"]),
            "reusableReason": row["reusable_reason"],
            "triggerDescription": row["trigger_description"],
            "inputsSchema": self.loads(row["inputs_schema"], {}),
            "draftSkillName": row["draft_skill_name"],
            "draftSkillDescription": row["draft_skill_description"],
            "draftSkillContent": row["draft_skill_content"],
            "draftTags": self.loads(row["draft_tags"], []),
            "granularity": row["granularity"],
            "requiresWorkflow": bool(row["requires_workflow"]),
            "suggestedSplit": self.loads(row["suggested_split"], []),
            "variableFlow": self.loads(row["variable_flow"], []),
            "riskNotes": row["risk_notes"],
            "confidence": float(row["confidence"] or 0),
            "status": row["status"],
            "registeredSkillId": row["registered_skill_id"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    def upsert(self, data: dict[str, Any]) -> dict[str, Any]:
        self.ensure_db()
        now = self.utc_now()
        created_at = data.get("created_at") or now
        updated_at = data.get("updated_at") or now
        with self.lock, self.connect() as conn:
            conn.execute(
                """
                INSERT INTO skill_evolution_records (
                    id, workspace_id, project_id, agent_id, agent_name, team_id, user_id,
                    source_session_id, source_run_id, source_message_id, router_gate,
                    workflow_evidence, reusable, reusable_reason, trigger_description,
                    inputs_schema, draft_skill_name, draft_skill_description, draft_skill_content,
                    draft_tags, granularity, requires_workflow, suggested_split, variable_flow,
                    risk_notes, confidence, status, registered_skill_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    workspace_id=excluded.workspace_id, project_id=excluded.project_id,
                    agent_id=excluded.agent_id, agent_name=excluded.agent_name,
                    team_id=excluded.team_id, user_id=excluded.user_id,
                    source_session_id=excluded.source_session_id,
                    source_run_id=excluded.source_run_id,
                    source_message_id=excluded.source_message_id,
                    router_gate=excluded.router_gate, workflow_evidence=excluded.workflow_evidence,
                    reusable=excluded.reusable, reusable_reason=excluded.reusable_reason,
                    trigger_description=excluded.trigger_description,
                    inputs_schema=excluded.inputs_schema, draft_skill_name=excluded.draft_skill_name,
                    draft_skill_description=excluded.draft_skill_description,
                    draft_skill_content=excluded.draft_skill_content,
                    draft_tags=excluded.draft_tags, granularity=excluded.granularity,
                    requires_workflow=excluded.requires_workflow,
                    suggested_split=excluded.suggested_split, variable_flow=excluded.variable_flow,
                    risk_notes=excluded.risk_notes, confidence=excluded.confidence,
                    status=excluded.status, registered_skill_id=excluded.registered_skill_id,
                    updated_at=excluded.updated_at
                """,
                (
                    data["id"], data.get("workspace_id") or "default",
                    data.get("project_id") or self.default_project_id, data["agent_id"],
                    data.get("agent_name") or "", data.get("team_id") or "",
                    data.get("user_id") or "default", data.get("source_session_id") or "",
                    data.get("source_run_id") or "", data.get("source_message_id") or "",
                    self.dumps(data.get("router_gate", {})),
                    self.dumps(data.get("workflow_evidence", {})),
                    1 if data.get("reusable") else 0, data.get("reusable_reason") or "",
                    data.get("trigger_description") or "", self.dumps(data.get("inputs_schema", {})),
                    data.get("draft_skill_name") or "", data.get("draft_skill_description") or "",
                    data.get("draft_skill_content") or "", self.dumps(data.get("draft_tags", [])),
                    data.get("granularity") or "", 1 if data.get("requires_workflow") else 0,
                    self.dumps(data.get("suggested_split", [])),
                    self.dumps(data.get("variable_flow", [])), data.get("risk_notes") or "",
                    float(data.get("confidence") or 0), data.get("status") or "skipped",
                    data.get("registered_skill_id") or "", created_at, updated_at,
                ),
            )
            row = conn.execute(
                "SELECT * FROM skill_evolution_records WHERE id = ?",
                (data["id"],),
            ).fetchone()
            conn.commit()
        return self._from_row(row)

    def get(self, record_id: str) -> dict[str, Any] | None:
        self.ensure_db()
        with self.lock, self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM skill_evolution_records WHERE id = ?",
                (record_id,),
            ).fetchone()
        return self._from_row(row) if row else None

    def list(
        self,
        *,
        status: str | None = None,
        agent_id: str | None = None,
        project_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        self.ensure_db()
        query = "SELECT * FROM skill_evolution_records WHERE project_id = ?"
        params: list[Any] = [project_id or self.default_project_id]
        if status:
            query += " AND status = ?"
            params.append(status)
        if agent_id:
            query += " AND agent_id = ?"
            params.append(agent_id)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(max(1, min(limit, 200)))
        with self.lock, self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._from_row(row) for row in rows]

    def mark_registered(self, record_id: str, *, registered_skill_id: str) -> dict[str, Any] | None:
        self.ensure_db()
        with self.lock, self.connect() as conn:
            conn.execute(
                """
                UPDATE skill_evolution_records
                SET status = 'registered', registered_skill_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (registered_skill_id, self.utc_now(), record_id),
            )
            row = conn.execute(
                "SELECT * FROM skill_evolution_records WHERE id = ?",
                (record_id,),
            ).fetchone()
            conn.commit()
        return self._from_row(row) if row else None

    def update_status(
        self,
        record_id: str,
        *,
        status: str,
        risk_notes: str | None = None,
        clear_registered_skill: bool = False,
    ) -> dict[str, Any] | None:
        self.ensure_db()
        assignments = ["status = ?", "updated_at = ?"]
        params: list[Any] = [status, self.utc_now()]
        if risk_notes is not None:
            assignments.append("risk_notes = ?")
            params.append(risk_notes)
        if clear_registered_skill:
            assignments.append("registered_skill_id = ''")
        params.append(record_id)
        with self.lock, self.connect() as conn:
            conn.execute(
                f"UPDATE skill_evolution_records SET {', '.join(assignments)} WHERE id = ?",
                params,
            )
            row = conn.execute(
                "SELECT * FROM skill_evolution_records WHERE id = ?",
                (record_id,),
            ).fetchone()
            conn.commit()
        return self._from_row(row) if row else None
