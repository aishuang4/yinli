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
    "capability_contract",
    "input_spec",
    "output_spec",
    "recipient_ids",
}
_JOB_UPDATE_FIELDS = (
    "workspace_id", "project_id", "name", "summary", "status", "pause_reason",
    "agent_id", "mode", "schedule_kind", "schedule_expr", "timezone", "model_config_id",
    "next_run_at", "last_run_at", "frozen_prompt", "skill_ids", "tool_scope",
    "capability_contract",
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
                        last_run_at, frozen_prompt, skill_ids, tool_scope, capability_contract,
                        input_spec, output_spec, missed_policy, failure_policy, delivery_policy,
                        delivery_channel_ref, recipient_ids, unattended_permission,
                        auto_send_enabled_at, required_successes, consecutive_successes,
                        consecutive_failures, max_consecutive_failures, max_firings_per_day,
                        created_from_session_id, created_at, updated_at, archived_at, model_config_id
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
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
        sql, params = self._job_update_sql(current, changes, self.utc_now())
        with self.lock, self.connect() as conn:
            try:
                conn.execute(sql, params)
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
        sql, params = self._firing_update_sql(current, changes, self.utc_now())
        with self.lock, self.connect() as conn:
            try:
                conn.execute(sql, params)
            except sqlite3.IntegrityError as exc:
                raise UniqueConstraintError("scheduled_job_runs", str(exc)) from exc
            conn.commit()
        return self.get_firing(firing_id)

    def update_firing_and_job(
        self,
        firing_id: str,
        firing_changes: dict[str, Any],
        job_id: str,
        job_changes: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """同一事务写 firing 终态与 Job 时钟，避免崩溃后同一到期点再跑。"""

        self.ensure_db()
        firing_current = self.get_firing(firing_id)
        job_current = self.get_job(job_id)
        if firing_current is None or job_current is None:
            return firing_current, job_current
        now = self.utc_now()
        firing_sql, firing_params = self._firing_update_sql(
            firing_current, firing_changes, now,
        )
        job_sql, job_params = self._job_update_sql(job_current, job_changes, now)
        with self.lock, self.connect() as conn:
            try:
                conn.execute(firing_sql, firing_params)
                conn.execute(job_sql, job_params)
            except sqlite3.IntegrityError as exc:
                table = "scheduled_jobs" if "scheduled_jobs" in str(exc) else "scheduled_job_runs"
                raise UniqueConstraintError(table, str(exc)) from exc
            conn.commit()
        return self.get_firing(firing_id), self.get_job(job_id)

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

    def list_due_jobs(self, now_iso: str) -> list[dict[str, Any]]:
        """active 且 next_run_at 已到期的 Job。"""

        self.ensure_db()
        with self.lock, self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM scheduled_jobs
                WHERE status = 'active'
                  AND next_run_at IS NOT NULL
                  AND next_run_at <= ?
                ORDER BY next_run_at ASC
                """,
                (now_iso,),
            ).fetchall()
        return [self._job_from_row(row) for row in rows]

    def get_firing_by_key(
        self,
        job_id: str,
        scheduled_for: str,
        trigger: str,
    ) -> dict[str, Any] | None:
        """按 UNIQUE (job_id, scheduled_for, trigger) 读取 firing。"""

        self.ensure_db()
        with self.lock, self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM scheduled_job_runs
                WHERE job_id = ? AND scheduled_for = ? AND trigger = ?
                """,
                (job_id, scheduled_for, trigger),
            ).fetchone()
        return self._firing_from_row(row) if row else None

    def list_non_terminal_firings(self) -> list[dict[str, Any]]:
        """全部 queued / starting / running firing，供启动 reconcile。"""

        self.ensure_db()
        with self.lock, self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM scheduled_job_runs
                WHERE status IN ('queued', 'starting', 'running')
                ORDER BY created_at ASC
                """,
            ).fetchall()
        return [self._firing_from_row(row) for row in rows]

    def list_starting_without_chat_run(self) -> list[dict[str, Any]]:
        """starting 且尚未拿到 chat_run_id 的 firing。"""

        self.ensure_db()
        with self.lock, self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM scheduled_job_runs
                WHERE status = 'starting'
                  AND (chat_run_id IS NULL OR chat_run_id = '')
                ORDER BY updated_at ASC
                """,
            ).fetchall()
        return [self._firing_from_row(row) for row in rows]

    def list_firings_for_scheduled_for(
        self,
        job_id: str,
        scheduled_for: str,
    ) -> list[dict[str, Any]]:
        """同一 Job 同一 scheduled_for 的全部 firing，不区分 trigger。"""

        self.ensure_db()
        with self.lock, self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM scheduled_job_runs
                WHERE job_id = ? AND scheduled_for = ?
                ORDER BY created_at ASC
                """,
                (job_id, scheduled_for),
            ).fetchall()
        return [self._firing_from_row(row) for row in rows]

    def list_stuck_clock_firings(self) -> list[dict[str, Any]]:
        """终态 schedule/catch_up 行仍占用 Job.next_run_at 的 firing。"""

        self.ensure_db()
        with self.lock, self.connect() as conn:
            rows = conn.execute(
                """
                SELECT r.*
                FROM scheduled_job_runs r
                INNER JOIN scheduled_jobs j ON j.id = r.job_id
                WHERE j.next_run_at IS NOT NULL
                  AND r.scheduled_for = j.next_run_at
                  AND r.trigger IN ('schedule', 'catch_up')
                  AND r.status IN (
                      'succeeded', 'failed', 'cancelled', 'missed', 'skipped'
                  )
                ORDER BY r.created_at ASC
                """,
            ).fetchall()
        return [self._firing_from_row(row) for row in rows]

    def _job_update_sql(
        self,
        current: dict[str, Any],
        changes: dict[str, Any],
        now: str,
    ) -> tuple[str, list[Any]]:
        merged = {**current, **changes, "id": current["id"], "updated_at": now}
        assignments = ["updated_at=?"]
        params: list[Any] = [merged["updated_at"]]
        for field in _JOB_UPDATE_FIELDS:
            if field not in changes:
                continue
            value = merged.get(field)
            if field in _JOB_JSON_FIELDS:
                if field == "tool_scope" and not value:
                    value = dict(_DEFAULT_TOOL_SCOPE)
                elif field == "capability_contract" and not value:
                    value = {}
                elif hasattr(value, "to_storage_dict"):
                    value = value.to_storage_dict()
                assignments.append(f"{field}=?")
                params.append(self.dumps(value if value is not None else _json_default(field)))
            else:
                assignments.append(f"{field}=?")
                params.append(value)
        params.append(current["id"])
        return (
            f"UPDATE scheduled_jobs SET {', '.join(assignments)} WHERE id = ?",
            params,
        )

    def _firing_update_sql(
        self,
        current: dict[str, Any],
        changes: dict[str, Any],
        now: str,
    ) -> tuple[str, list[Any]]:
        merged = {**current, **changes, "id": current["id"], "updated_at": now}
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
        params.append(current["id"])
        return (
            f"UPDATE scheduled_job_runs SET {', '.join(assignments)} WHERE id = ?",
            params,
        )

    def insert_notification(self, record: dict[str, Any]) -> dict[str, Any]:
        self.ensure_db()
        now = self.utc_now()
        note_id = record.get("id") or f"ntf-{uuid.uuid4().hex}"
        payload = {
            "id": note_id,
            "workspace_id": record.get("workspace_id") or self.default_workspace_id,
            "project_id": record.get("project_id") or "",
            "job_id": record.get("job_id") or "",
            "firing_id": record.get("firing_id") or "",
            "kind": record["kind"],
            "title": record["title"],
            "body": record.get("body") or "",
            "payload": record.get("payload") or {},
            "read_at": record.get("read_at"),
            "created_at": record.get("created_at") or now,
        }
        with self.lock, self.connect() as conn:
            conn.execute(
                """
                INSERT INTO user_notifications (
                    id, workspace_id, project_id, job_id, firing_id, kind, title,
                    body, payload, read_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["id"],
                    payload["workspace_id"],
                    payload["project_id"],
                    payload["job_id"],
                    payload["firing_id"],
                    payload["kind"],
                    payload["title"],
                    payload["body"],
                    self.dumps(payload["payload"]),
                    payload["read_at"],
                    payload["created_at"],
                ),
            )
            conn.commit()
        stored = self.get_notification(note_id)
        assert stored is not None
        return stored

    def get_notification(self, note_id: str) -> dict[str, Any] | None:
        self.ensure_db()
        with self.lock, self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM user_notifications WHERE id = ?",
                (note_id,),
            ).fetchone()
        return self._notification_from_row(row) if row else None

    def list_notifications(
        self,
        *,
        project_id: str | None = None,
        unread_only: bool = False,
        limit: int = 50,
        before: str | None = None,
    ) -> list[dict[str, Any]]:
        self.ensure_db()
        clauses: list[str] = []
        params: list[Any] = []
        if project_id:
            clauses.append("project_id = ?")
            params.append(project_id)
        if unread_only:
            clauses.append("read_at IS NULL")
        if before:
            clauses.append("created_at < ?")
            params.append(before)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, limit))
        with self.lock, self.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM user_notifications {where} ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [self._notification_from_row(row) for row in rows]

    def count_unread_notifications(self, *, project_id: str | None = None) -> int:
        self.ensure_db()
        if project_id:
            sql = "SELECT COUNT(*) AS n FROM user_notifications WHERE read_at IS NULL AND project_id = ?"
            params: tuple[Any, ...] = (project_id,)
        else:
            sql = "SELECT COUNT(*) AS n FROM user_notifications WHERE read_at IS NULL"
            params = ()
        with self.lock, self.connect() as conn:
            row = conn.execute(sql, params).fetchone()
        return int(row["n"] if row is not None else 0)

    def mark_notification_read(self, note_id: str) -> dict[str, Any] | None:
        current = self.get_notification(note_id)
        if current is None:
            return None
        if current.get("read_at"):
            return current
        stamped = self.utc_now()
        with self.lock, self.connect() as conn:
            conn.execute(
                "UPDATE user_notifications SET read_at = ? WHERE id = ? AND read_at IS NULL",
                (stamped, note_id),
            )
            conn.commit()
        return self.get_notification(note_id)

    def mark_all_notifications_read(self, *, project_id: str | None = None) -> int:
        self.ensure_db()
        stamped = self.utc_now()
        if project_id:
            sql = "UPDATE user_notifications SET read_at = ? WHERE read_at IS NULL AND project_id = ?"
            params: tuple[Any, ...] = (stamped, project_id)
        else:
            sql = "UPDATE user_notifications SET read_at = ? WHERE read_at IS NULL"
            params = (stamped,)
        with self.lock, self.connect() as conn:
            cursor = conn.execute(sql, params)
            conn.commit()
            return int(cursor.rowcount)

    def _notification_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "workspace_id": row["workspace_id"],
            "project_id": row["project_id"],
            "job_id": row["job_id"],
            "firing_id": row["firing_id"],
            "kind": row["kind"],
            "title": row["title"],
            "body": row["body"],
            "payload": self.loads(row["payload"], {}),
            "read_at": row["read_at"],
            "created_at": row["created_at"],
        }

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
            self.dumps(_contract_storage(record.get("capability_contract"))),
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
            record.get("model_config_id") or "",
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
            "model_config_id": row["model_config_id"],
            "mode": row["mode"],
            "schedule_kind": row["schedule_kind"],
            "schedule_expr": self.loads(row["schedule_expr"], {}),
            "timezone": row["timezone"],
            "next_run_at": row["next_run_at"],
            "last_run_at": row["last_run_at"],
            "frozen_prompt": row["frozen_prompt"],
            "skill_ids": self.loads(row["skill_ids"], []),
            "tool_scope": tool_scope,
            "capability_contract": _contract_from_row(row, self.loads),
            "input_spec": self.loads(row["input_spec"], {}),
            "output_spec": self.loads(row["output_spec"], {}),
            "missed_policy": row["missed_policy"],
            "failure_policy": row["failure_policy"],
            "delivery_policy": row["delivery_policy"],
            "delivery_channel_ref": row["delivery_channel_ref"],
            "recipient_ids": self.loads(row["recipient_ids"], []),
            "unattended_permission": row["unattended_permission"],
            "execution_policy": self.loads(row["execution_policy"], {}) if "execution_policy" in row.keys() else {},
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


def _contract_storage(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    if hasattr(value, "to_storage_dict"):
        return value.to_storage_dict()
    if isinstance(value, dict):
        return value
    return {}


def _contract_from_row(row: sqlite3.Row, loads: Callable[[str | None, Any], Any]) -> dict[str, Any]:
    try:
        raw = row["capability_contract"]
    except (IndexError, KeyError):
        return {}
    loaded = loads(raw, {})
    return loaded if isinstance(loaded, dict) else {}


def _json_default(field: str) -> Any:
    if field in {"skill_ids", "recipient_ids"}:
        return []
    if field == "tool_scope":
        return dict(_DEFAULT_TOOL_SCOPE)
    if field == "capability_contract":
        return {}
    return {}


class UniqueConstraintError(Exception):
    """SQLite UNIQUE 冲突。"""

    def __init__(self, table: str, detail: str) -> None:
        super().__init__(detail)
        self.table = table
        self.detail = detail
