"""定时任务、firing、收件人白名单与应用内通知的 SQLite 存储。"""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Callable
from typing import Any

# 缺省与 Job 冻结 scope 全 false JSON 对齐。
_DEFAULT_TOOL_SCOPE = {
    "allow_bash": False,
    "allow_http": False,
    "allow_web": False,
    "allow_mcp": False,
    "allow_knowledge": False,
}

_JOB_JSON_FIELDS = {
    "schedule_expr",
    "skill_ids",
    "tool_scope",
    "input_spec",
    "output_spec",
    "recipient_ids",
}
_JOB_UPDATE_FIELDS = (
    "workspace_id", "project_id", "name", "summary", "status", "pause_reason",
    "agent_id", "mode", "schedule_kind", "schedule_expr", "timezone",
    "next_run_at", "last_run_at", "frozen_prompt", "skill_ids", "tool_scope",
    "input_spec", "output_spec", "missed_policy", "failure_policy",
    "delivery_policy", "delivery_channel_ref", "recipient_ids",
    "unattended_permission", "auto_send_enabled_at", "required_successes",
    "consecutive_successes", "consecutive_failures", "max_consecutive_failures",
    "max_firings_per_day", "created_from_session_id", "archived_at",
)
_FIRING_JSON_FIELDS = {"output_manifest"}
_FIRING_UPDATE_FIELDS = (
    "job_id", "workspace_id", "project_id", "trigger", "scheduled_for", "status",
    "chat_session_id", "chat_run_id", "error_code", "error_message",
    "output_manifest", "delivery_status", "started_at", "completed_at",
)


class ScheduledJobRecordStore:
    """封装 scheduled_jobs / scheduled_job_runs / 白名单 / 通知表。"""

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

    def insert_job(self, record: dict[str, Any]) -> dict[str, Any]:
        self.ensure_db()
        now = self.utc_now()
        job_id = record.get("id") or f"job-{uuid.uuid4().hex}"
        payload = {**record, "id": job_id}
        payload.setdefault("created_at", now)
        payload.setdefault("updated_at", now)
        with self.lock, self.connect() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO scheduled_jobs (
                        id, workspace_id, project_id, name, summary, status, pause_reason,
                        agent_id, mode, schedule_kind, schedule_expr, timezone, next_run_at,
                        last_run_at, frozen_prompt, skill_ids, tool_scope, input_spec,
                        output_spec, missed_policy, failure_policy, delivery_policy,
                        delivery_channel_ref, recipient_ids, unattended_permission,
                        auto_send_enabled_at, required_successes, consecutive_successes,
                        consecutive_failures, max_consecutive_failures, max_firings_per_day,
                        created_from_session_id, created_at, updated_at, archived_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    self._job_params(payload),
                )
            except sqlite3.IntegrityError as exc:
                raise UniqueConstraintError("scheduled_jobs", str(exc)) from exc
            conn.commit()
        stored = self.get_job(job_id)
        assert stored is not None
        return stored

    def update_job(self, job_id: str, changes: dict[str, Any]) -> dict[str, Any] | None:
        self.ensure_db()
        current = self.get_job(job_id)
        if current is None:
            return None
        merged = {**current, **changes, "id": job_id, "updated_at": self.utc_now()}
        assignments = ["updated_at=?"]
        params: list[Any] = [merged["updated_at"]]
        for field in _JOB_UPDATE_FIELDS:
            if field not in changes:
                continue
            value = merged.get(field)
            if field in _JOB_JSON_FIELDS:
                if field == "tool_scope" and not value:
                    value = dict(_DEFAULT_TOOL_SCOPE)
                elif hasattr(value, "to_storage_dict"):
                    value = value.to_storage_dict()
                assignments.append(f"{field}=?")
                params.append(self.dumps(value if value is not None else _json_default(field)))
            else:
                assignments.append(f"{field}=?")
                params.append(value)
        params.append(job_id)
        with self.lock, self.connect() as conn:
            try:
                conn.execute(
                    f"UPDATE scheduled_jobs SET {', '.join(assignments)} WHERE id = ?",
                    params,
                )
            except sqlite3.IntegrityError as exc:
                raise UniqueConstraintError("scheduled_jobs", str(exc)) from exc
            conn.commit()
        return self.get_job(job_id)

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        self.ensure_db()
        with self.lock, self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM scheduled_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        return self._job_from_row(row) if row else None

    def list_jobs(
        self,
        *,
        project_id: str | None = None,
        agent_id: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        self.ensure_db()
        clauses: list[str] = []
        params: list[Any] = []
        if project_id:
            clauses.append("project_id = ?")
            params.append(project_id)
        if agent_id:
            clauses.append("agent_id = ?")
            params.append(agent_id)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.lock, self.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM scheduled_jobs {where} ORDER BY updated_at DESC",
                params,
            ).fetchall()
        return [self._job_from_row(row) for row in rows]

    def delete_job(self, job_id: str) -> None:
        self.ensure_db()
        with self.lock, self.connect() as conn:
            conn.execute("DELETE FROM scheduled_jobs WHERE id = ?", (job_id,))
            conn.commit()

    def insert_firing(self, record: dict[str, Any]) -> dict[str, Any]:
        self.ensure_db()
        now = self.utc_now()
        firing_id = record.get("id") or f"firing-{uuid.uuid4().hex}"
        payload = {**record, "id": firing_id}
        payload.setdefault("created_at", now)
        payload.setdefault("updated_at", now)
        with self.lock, self.connect() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO scheduled_job_runs (
                        id, job_id, workspace_id, project_id, trigger, scheduled_for,
                        status, chat_session_id, chat_run_id, error_code, error_message,
                        output_manifest, delivery_status, started_at, completed_at,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    self._firing_params(payload),
                )
            except sqlite3.IntegrityError as exc:
                raise UniqueConstraintError("scheduled_job_runs", str(exc)) from exc
            conn.commit()
        stored = self.get_firing(firing_id)
        assert stored is not None
        return stored

    def update_firing(self, firing_id: str, changes: dict[str, Any]) -> dict[str, Any] | None:
        self.ensure_db()
        current = self.get_firing(firing_id)
        if current is None:
            return None
        merged = {**current, **changes, "id": firing_id, "updated_at": self.utc_now()}
        assignments = ["updated_at=?"]
        params: list[Any] = [merged["updated_at"]]
        for field in _FIRING_UPDATE_FIELDS:
            if field not in changes:
                continue
            value = merged.get(field)
            assignments.append(f"{field}=?")
            if field in _FIRING_JSON_FIELDS:
                params.append(self.dumps(value or {}))
            else:
                params.append(value)
        params.append(firing_id)
        with self.lock, self.connect() as conn:
            try:
                conn.execute(
                    f"UPDATE scheduled_job_runs SET {', '.join(assignments)} WHERE id = ?",
                    params,
                )
            except sqlite3.IntegrityError as exc:
                raise UniqueConstraintError("scheduled_job_runs", str(exc)) from exc
            conn.commit()
        return self.get_firing(firing_id)

    def get_firing(self, firing_id: str) -> dict[str, Any] | None:
        self.ensure_db()
        with self.lock, self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM scheduled_job_runs WHERE id = ?",
                (firing_id,),
            ).fetchone()
        return self._firing_from_row(row) if row else None

    def list_firings(
        self,
        job_id: str,
        *,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        self.ensure_db()
        if status:
            sql = """
                SELECT * FROM scheduled_job_runs
                WHERE job_id = ? AND status = ?
                ORDER BY created_at DESC
            """
            params: tuple[Any, ...] = (job_id, status)
        else:
            sql = """
                SELECT * FROM scheduled_job_runs
                WHERE job_id = ?
                ORDER BY created_at DESC
            """
            params = (job_id,)
        with self.lock, self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._firing_from_row(row) for row in rows]

    def _job_params(self, record: dict[str, Any]) -> tuple[Any, ...]:
        tool_scope = record.get("tool_scope")
        if not tool_scope:
            tool_scope = dict(_DEFAULT_TOOL_SCOPE)
        elif hasattr(tool_scope, "to_storage_dict"):
            tool_scope = tool_scope.to_storage_dict()
        return (
            record["id"],
            record.get("workspace_id") or self.default_workspace_id,
            record.get("project_id") or self.default_project_id,
            record["name"],
            record.get("summary") or "",
            record["status"],
            record.get("pause_reason") or "",
            record["agent_id"],
            record.get("mode") or "agent",
            record["schedule_kind"],
            self.dumps(record.get("schedule_expr") or {}),
            record["timezone"],
            record.get("next_run_at"),
            record.get("last_run_at"),
            record.get("frozen_prompt") or "",
            self.dumps(record.get("skill_ids") or []),
            self.dumps(tool_scope),
            self.dumps(record.get("input_spec") or {}),
            self.dumps(record.get("output_spec") or {}),
            record.get("missed_policy") or "catch_up_once",
            record.get("failure_policy") or "notify_keep_schedule",
            record.get("delivery_policy") or "notify",
            record.get("delivery_channel_ref") or "",
            self.dumps(record.get("recipient_ids") or []),
            record.get("unattended_permission") or "overlay_workspace_write",
            record.get("auto_send_enabled_at"),
            int(record.get("required_successes") or 3),
            int(record.get("consecutive_successes") or 0),
            int(record.get("consecutive_failures") or 0),
            int(record.get("max_consecutive_failures") or 3),
            int(record.get("max_firings_per_day") or 8),
            record.get("created_from_session_id") or "",
            record["created_at"],
            record["updated_at"],
            record.get("archived_at"),
        )

    def _job_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        tool_scope = self.loads(row["tool_scope"], dict(_DEFAULT_TOOL_SCOPE))
        if not isinstance(tool_scope, dict):
            tool_scope = dict(_DEFAULT_TOOL_SCOPE)
        return {
            "id": row["id"],
            "workspace_id": row["workspace_id"],
            "project_id": row["project_id"],
            "name": row["name"],
            "summary": row["summary"],
            "status": row["status"],
            "pause_reason": row["pause_reason"],
            "agent_id": row["agent_id"],
            "mode": row["mode"],
            "schedule_kind": row["schedule_kind"],
            "schedule_expr": self.loads(row["schedule_expr"], {}),
            "timezone": row["timezone"],
            "next_run_at": row["next_run_at"],
            "last_run_at": row["last_run_at"],
            "frozen_prompt": row["frozen_prompt"],
            "skill_ids": self.loads(row["skill_ids"], []),
            "tool_scope": tool_scope,
            "input_spec": self.loads(row["input_spec"], {}),
            "output_spec": self.loads(row["output_spec"], {}),
            "missed_policy": row["missed_policy"],
            "failure_policy": row["failure_policy"],
            "delivery_policy": row["delivery_policy"],
            "delivery_channel_ref": row["delivery_channel_ref"],
            "recipient_ids": self.loads(row["recipient_ids"], []),
            "unattended_permission": row["unattended_permission"],
            "auto_send_enabled_at": row["auto_send_enabled_at"],
            "required_successes": row["required_successes"],
            "consecutive_successes": row["consecutive_successes"],
            "consecutive_failures": row["consecutive_failures"],
            "max_consecutive_failures": row["max_consecutive_failures"],
            "max_firings_per_day": row["max_firings_per_day"],
            "created_from_session_id": row["created_from_session_id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "archived_at": row["archived_at"],
        }

    def _firing_params(self, record: dict[str, Any]) -> tuple[Any, ...]:
        return (
            record["id"],
            record["job_id"],
            record.get("workspace_id") or self.default_workspace_id,
            record.get("project_id") or self.default_project_id,
            record["trigger"],
            record["scheduled_for"],
            record["status"],
            record.get("chat_session_id") or "",
            record.get("chat_run_id") or "",
            record.get("error_code") or "",
            record.get("error_message") or "",
            self.dumps(record.get("output_manifest") or {}),
            record.get("delivery_status") or "not_applicable",
            record.get("started_at"),
            record.get("completed_at"),
            record["created_at"],
            record["updated_at"],
        )

    def _firing_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "job_id": row["job_id"],
            "workspace_id": row["workspace_id"],
            "project_id": row["project_id"],
            "trigger": row["trigger"],
            "scheduled_for": row["scheduled_for"],
            "status": row["status"],
            "chat_session_id": row["chat_session_id"],
            "chat_run_id": row["chat_run_id"],
            "error_code": row["error_code"],
            "error_message": row["error_message"],
            "output_manifest": self.loads(row["output_manifest"], {}),
            "delivery_status": row["delivery_status"],
            "started_at": row["started_at"],
            "completed_at": row["completed_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }


def _json_default(field: str) -> Any:
    if field in {"skill_ids", "recipient_ids"}:
        return []
    if field == "tool_scope":
        return dict(_DEFAULT_TOOL_SCOPE)
    return {}


class UniqueConstraintError(Exception):
    """SQLite UNIQUE 冲突。"""

    def __init__(self, table: str, detail: str) -> None:
        super().__init__(detail)
        self.table = table
        self.detail = detail
