"""群聊工作室 SQLite 存储。只写 group_* 表，禁止直接 SQL 写入 Chat 会话表。"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any


ACTIVE_STUDIO_STATUSES = (
    "drafting",
    "discussing",
    "executing",
    "reviewing",
    "awaiting_human",
    "paused",
    "circuit_broken",
    "closing",
)


class GroupStudioRecordStore:
    """封装 group_* 表读写；JSON 空值在本层从 SQL '' / 0 转换。"""

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

    def get_active_by_chat_session(self, chat_session_id: str) -> dict[str, Any] | None:
        """未归档/失败的工作室；一会话最多一间。"""

        self.ensure_db()
        with self.lock, self.connect() as conn:
            row = conn.execute(
                """
                SELECT id FROM group_studios
                WHERE chat_session_id = ? AND status NOT IN ('archived', 'failed')
                LIMIT 1
                """,
                (chat_session_id,),
            ).fetchone()
        if row is None:
            return None
        return self.get_snapshot(str(row["id"]))

    def lookup_active_by_chat_session_ids(
        self,
        session_ids: list[str],
    ) -> dict[str, dict[str, str]]:
        """catalog 用：session_id -> {id, status, title}。"""

        if not session_ids:
            return {}
        self.ensure_db()
        placeholders = ",".join("?" for _ in session_ids)
        with self.lock, self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT id, chat_session_id, status, title FROM group_studios
                WHERE chat_session_id IN ({placeholders})
                  AND status NOT IN ('archived', 'failed')
                """,
                tuple(session_ids),
            ).fetchall()
        return {
            str(row["chat_session_id"]): {
                "id": str(row["id"]),
                "status": str(row["status"]),
                "title": str(row["title"]),
            }
            for row in rows
        }

    _BLANK_DRAFT_SQL = """
        s.status = 'drafting'
        AND TRIM(COALESCE(t.goal, '')) = ''
        AND TRIM(COALESCE(t.dod_json, '[]')) IN ('', '[]')
        AND (
            SELECT COUNT(*) FROM group_memberships mb
            WHERE mb.group_id = s.id AND mb.status != 'removed'
        ) <= 2
        AND NOT EXISTS (
            SELECT 1 FROM group_messages m
            WHERE m.group_id = s.id
              AND IFNULL(m.visible_in_timeline, 1) = 1
              AND TRIM(IFNULL(m.body, '')) != ''
        )
        AND NOT EXISTS (SELECT 1 FROM group_artifacts a WHERE a.group_id = s.id)
        AND NOT EXISTS (SELECT 1 FROM group_runs r WHERE r.group_id = s.id)
    """

    def list_active_by_project(self, project_id: str) -> list[dict[str, Any]]:
        """列出某 Project 下未归档工作室，供群聊侧栏独立于 Chat catalog。

        无目标、无可见消息、无产物、无 Run 的 drafting 空白稿不进历史。
        """

        self.ensure_db()
        with self.lock, self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT s.id, s.title, s.status, s.project_id, s.workspace_id,
                       s.chat_session_id, s.updated_at
                FROM group_studios s
                JOIN group_task_cards t ON t.id = s.current_task_card_id
                WHERE s.project_id = ? AND s.status NOT IN ('archived', 'failed')
                  AND NOT ({self._BLANK_DRAFT_SQL})
                ORDER BY s.updated_at DESC
                """,
                (project_id,),
            ).fetchall()
        return [
            {
                "id": str(row["id"]),
                "title": str(row["title"] or "未命名工作室"),
                "status": str(row["status"]),
                "projectId": str(row["project_id"]),
                "workspaceId": str(row["workspace_id"]),
                "chatSessionId": str(row["chat_session_id"]),
                "updatedAt": str(row["updated_at"]),
            }
            for row in rows
        ]

    def find_blank_draft_id(self, project_id: str) -> str | None:
        """同一 Project 下最新一份空白 drafting 工作室。"""

        self.ensure_db()
        with self.lock, self.connect() as conn:
            row = conn.execute(
                f"""
                SELECT s.id
                FROM group_studios s
                JOIN group_task_cards t ON t.id = s.current_task_card_id
                WHERE s.project_id = ? AND {self._BLANK_DRAFT_SQL}
                ORDER BY s.updated_at DESC
                LIMIT 1
                """,
                (project_id,),
            ).fetchone()
        if row is None:
            return None
        return str(row["id"])

    def get_snapshot(self, group_id: str, *, message_limit: int = 100) -> dict[str, Any] | None:
        """组装 GET /{id} 形状的快照。"""

        self.ensure_db()
        with self.lock, self.connect() as conn:
            studio = conn.execute(
                "SELECT * FROM group_studios WHERE id = ?",
                (group_id,),
            ).fetchone()
            if studio is None:
                return None
            task = conn.execute(
                "SELECT * FROM group_task_cards WHERE id = ?",
                (studio["current_task_card_id"],),
            ).fetchone()
            blackboard = conn.execute(
                "SELECT * FROM group_blackboards WHERE group_id = ?",
                (group_id,),
            ).fetchone()
            members = conn.execute(
                """
                SELECT * FROM group_memberships
                WHERE group_id = ?
                ORDER BY created_at ASC
                """,
                (group_id,),
            ).fetchall()
            messages = conn.execute(
                """
                SELECT * FROM group_messages
                WHERE group_id = ?
                ORDER BY sort_order DESC
                LIMIT ?
                """,
                (group_id, message_limit),
            ).fetchall()
            pending = conn.execute(
                """
                SELECT * FROM group_approvals
                WHERE group_id = ? AND status = 'pending'
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (group_id,),
            ).fetchone()
        if task is None or blackboard is None:
            return None
        ordered_messages = list(reversed(messages))
        status = str(studio["status"])
        hide_approval = status in {"paused", "circuit_broken", "awaiting_human"}
        return {
            "id": studio["id"],
            "projectId": studio["project_id"],
            "workspaceId": studio["workspace_id"],
            "chatSessionId": studio["chat_session_id"],
            "title": studio["title"],
            "status": status,
            "phaseBeforePause": studio["phase_before_pause"] or None,
            "currentTaskCardId": studio["current_task_card_id"],
            "taskCard": self._task_card_from_row(task),
            "blackboard": self._blackboard_from_row(blackboard, studio["current_task_card_id"]),
            "members": [self._member_from_row(row) for row in members],
            "budget": self.loads(studio["budget_json"], {
                "maxTurns": 40,
                "maxNoProgressTurns": 3,
                "usedTurns": 0,
                "noProgressTurns": 0,
            }),
            "currentRunId": studio["current_run_id"] or None,
            "pendingSpeakerMemberId": (
                (str(studio["pending_speaker_member_id"] or "") or None)
                if "pending_speaker_member_id" in studio.keys()
                else None
            ),
            "currentApproval": None if hide_approval else self._approval_from_row(pending),
            "messages": [self._message_from_row(row) for row in ordered_messages],
            "awaitingRestatement": bool(studio["awaiting_restatement"]),
            "createdAt": studio["created_at"],
            "updatedAt": studio["updated_at"],
        }

    def create_from_chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        """事务创建工作室、任务卡、黑板、成员与可选附件。冲突则返回已有活跃工作室。"""

        self.ensure_db()
        now = payload.get("createdAt") or self.utc_now()
        with self.lock, self.connect() as conn:
            existing = conn.execute(
                """
                SELECT id FROM group_studios
                WHERE chat_session_id = ? AND status NOT IN ('archived', 'failed')
                LIMIT 1
                """,
                (payload["chatSessionId"],),
            ).fetchone()
            if existing is not None:
                group_id = str(existing["id"])
            else:
                group_id = payload["id"]
                try:
                    self._insert_new_studio(conn, payload, now)
                except sqlite3.IntegrityError:
                    raced = conn.execute(
                        """
                        SELECT id FROM group_studios
                        WHERE chat_session_id = ? AND status NOT IN ('archived', 'failed')
                        LIMIT 1
                        """,
                        (payload["chatSessionId"],),
                    ).fetchone()
                    if raced is None:
                        raise
                    group_id = str(raced["id"])
        snapshot = self.get_snapshot(group_id)
        if snapshot is None:
            raise RuntimeError(f"group studio {group_id} 写入后无法读取")
        return snapshot

    def has_runs(self, group_id: str) -> bool:
        """是否已经有过 GroupRun。PR 5 之前为 false，决定要不要走 restatement。"""

        self.ensure_db()
        with self.lock, self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM group_runs WHERE group_id = ? LIMIT 1",
                (group_id,),
            ).fetchone()
        return row is not None

    def count_active_members(self, group_id: str) -> int:
        self.ensure_db()
        with self.lock, self.connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS n FROM group_memberships
                WHERE group_id = ? AND status != 'removed'
                """,
                (group_id,),
            ).fetchone()
        return int(row["n"] if row is not None else 0)

    def find_active_member_by_agent(self, group_id: str, agent_id: str) -> dict[str, Any] | None:
        self.ensure_db()
        with self.lock, self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM group_memberships
                WHERE group_id = ? AND agent_id = ? AND status != 'removed'
                LIMIT 1
                """,
                (group_id, agent_id),
            ).fetchone()
        return self._member_from_row(row) if row is not None else None

    def insert_member(self, group_id: str, member: dict[str, Any]) -> dict[str, Any]:
        self.ensure_db()
        now = self.utc_now()
        with self.lock, self.connect() as conn:
            conn.execute(
                """
                INSERT INTO group_memberships (
                    id, group_id, role, agent_id, display_name, status, muted,
                    last_spoke_at, crash_count, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    member["id"],
                    group_id,
                    member["role"],
                    member["agentId"],
                    member["displayName"],
                    member.get("status") or "active",
                    1 if member.get("muted") else 0,
                    None,
                    int(member.get("crashCount") or 0),
                    now,
                    now,
                ),
            )
        found = self.find_active_member_by_agent(group_id, member["agentId"])
        if found is None:
            raise RuntimeError("member insert failed")
        return found

    def get_member(self, group_id: str, member_id: str) -> dict[str, Any] | None:
        self.ensure_db()
        with self.lock, self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM group_memberships WHERE group_id = ? AND id = ?",
                (group_id, member_id),
            ).fetchone()
        return self._member_from_row(row) if row is not None else None

    def set_member_muted(self, group_id: str, member_id: str, muted: bool) -> dict[str, Any] | None:
        self.ensure_db()
        now = self.utc_now()
        with self.lock, self.connect() as conn:
            conn.execute(
                """
                UPDATE group_memberships
                SET muted = ?, updated_at = ?
                WHERE group_id = ? AND id = ?
                """,
                (1 if muted else 0, now, group_id, member_id),
            )
        return self.get_member(group_id, member_id)

    def set_member_last_spoke(self, group_id: str, member_id: str, spoke_at: str | None = None) -> None:
        """工人/主持人可见发言后更新 last_spoke_at。"""

        if not group_id or not member_id or member_id == "human":
            return
        self.ensure_db()
        now = spoke_at or self.utc_now()
        with self.lock, self.connect() as conn:
            conn.execute(
                """
                UPDATE group_memberships
                SET last_spoke_at = ?, updated_at = ?
                WHERE group_id = ? AND id = ?
                """,
                (now, now, group_id, member_id),
            )

    def set_member_removed(self, group_id: str, member_id: str) -> dict[str, Any] | None:
        self.ensure_db()
        now = self.utc_now()
        with self.lock, self.connect() as conn:
            conn.execute(
                """
                UPDATE group_memberships
                SET status = 'removed', updated_at = ?
                WHERE group_id = ? AND id = ?
                """,
                (now, group_id, member_id),
            )
        return self.get_member(group_id, member_id)

    def set_studio_status(self, group_id: str, status: str) -> None:
        self.ensure_db()
        now = self.utc_now()
        with self.lock, self.connect() as conn:
            conn.execute(
                "UPDATE group_studios SET status = ?, updated_at = ? WHERE id = ?",
                (status, now, group_id),
            )

    def update_studio_state(
        self,
        group_id: str,
        *,
        status: str | None = None,
        phase_before_pause: str | None = None,
        clear_phase_before_pause: bool = False,
        awaiting_restatement: bool | None = None,
        budget: dict[str, Any] | None = None,
        current_run_id: str | None = None,
        clear_current_run: bool = False,
    ) -> dict[str, Any]:
        """部分更新工作室生命周期字段。"""

        self.ensure_db()
        now = self.utc_now()
        assignments: list[str] = ["updated_at = ?"]
        params: list[Any] = [now]
        if status is not None:
            assignments.append("status = ?")
            params.append(status)
        if clear_phase_before_pause:
            assignments.append("phase_before_pause = ?")
            params.append(None)
        elif phase_before_pause is not None:
            assignments.append("phase_before_pause = ?")
            params.append(phase_before_pause)
        if awaiting_restatement is not None:
            assignments.append("awaiting_restatement = ?")
            params.append(1 if awaiting_restatement else 0)
        if budget is not None:
            assignments.append("budget_json = ?")
            params.append(self.dumps(budget))
        if clear_current_run:
            assignments.append("current_run_id = ?")
            params.append("")
        elif current_run_id is not None:
            assignments.append("current_run_id = ?")
            params.append(current_run_id)
        params.append(group_id)
        with self.lock, self.connect() as conn:
            conn.execute(
                f"UPDATE group_studios SET {', '.join(assignments)} WHERE id = ?",
                tuple(params),
            )
        return self.get_snapshot(group_id) or {}

    def set_current_run_id(self, group_id: str, run_id: str | None) -> None:
        self.ensure_db()
        now = self.utc_now()
        with self.lock, self.connect() as conn:
            conn.execute(
                "UPDATE group_studios SET current_run_id = ?, updated_at = ? WHERE id = ?",
                (run_id or "", now, group_id),
            )

    def set_pending_speaker(self, group_id: str, member_id: str | None) -> None:
        """写入或清空尚未开工的指派工人。"""

        self.ensure_db()
        now = self.utc_now()
        with self.lock, self.connect() as conn:
            conn.execute(
                """
                UPDATE group_studios
                SET pending_speaker_member_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (member_id or "", now, group_id),
            )

    def get_pending_speaker(self, group_id: str) -> str:
        """读取尚未开工的指派工人；没有则空字符串。"""

        self.ensure_db()
        with self.lock, self.connect() as conn:
            row = conn.execute(
                "SELECT pending_speaker_member_id FROM group_studios WHERE id = ?",
                (group_id,),
            ).fetchone()
        if row is None:
            return ""
        return str(row["pending_speaker_member_id"] or "")

    def insert_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.ensure_db()
        now = self.utc_now()
        run_id = payload["id"]
        with self.lock, self.connect() as conn:
            conn.execute(
                """
                INSERT INTO group_runs (
                    id, group_id, kind, status, speaker_member_id, error,
                    token_input, token_output, created_at, completed_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    payload["groupId"],
                    payload.get("kind") or "discussion",
                    payload.get("status") or "running",
                    payload.get("speakerMemberId") or "",
                    "",
                    0,
                    0,
                    now,
                    None,
                    now,
                ),
            )
        return {"id": run_id, "createdAt": now}

    def update_run_status(self, run_id: str, status: str, error: str = "") -> None:
        self.ensure_db()
        now = self.utc_now()
        completed = now if status in {"completed", "cancelled", "failed"} else None
        with self.lock, self.connect() as conn:
            conn.execute(
                """
                UPDATE group_runs
                SET status = ?, error = ?, completed_at = COALESCE(?, completed_at), updated_at = ?
                WHERE id = ?
                """,
                (status, error, completed, now, run_id),
            )

    def next_message_sort_order(self, group_id: str) -> int:
        self.ensure_db()
        with self.lock, self.connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(sort_order), 0) AS n FROM group_messages WHERE group_id = ?",
                (group_id,),
            ).fetchone()
        return int(row["n"] if row is not None else 0) + 1

    def insert_message(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.ensure_db()
        now = payload.get("createdAt") or self.utc_now()
        sort_order = int(payload.get("sortOrder") or self.next_message_sort_order(payload["groupId"]))
        with self.lock, self.connect() as conn:
            conn.execute(
                """
                INSERT INTO group_messages (
                    id, group_id, run_id, speaker_id, role, intent, body, thinking,
                    mentions_json, attachments_json, visible_in_timeline,
                    created_at, sort_order
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["id"],
                    payload["groupId"],
                    payload.get("runId") or "",
                    payload["speakerId"],
                    payload["role"],
                    payload.get("intent") or "discuss",
                    payload.get("body") or "",
                    payload.get("thinking") or "",
                    self.dumps(payload.get("mentions") or []),
                    self.dumps(payload.get("attachments") or []),
                    1 if payload.get("visibleInTimeline", True) else 0,
                    now,
                    sort_order,
                ),
            )
        return {**payload, "createdAt": now, "sortOrder": sort_order}

    def update_message_body(self, message_id: str, body: str, thinking: str | None = None) -> None:
        self.ensure_db()
        with self.lock, self.connect() as conn:
            if thinking is None:
                conn.execute(
                    "UPDATE group_messages SET body = ? WHERE id = ?",
                    (body, message_id),
                )
            else:
                conn.execute(
                    "UPDATE group_messages SET body = ?, thinking = ? WHERE id = ?",
                    (body, thinking, message_id),
                )

    def append_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        """持久化一条非 DELTA 事件，返回带 seq 的信封。"""

        self.ensure_db()
        now = payload.get("createdAt") or self.utc_now()
        event_id = payload.get("id") or f"gevt_{now}"
        with self.lock, self.connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(sort_order), 0) AS n FROM group_studio_events WHERE group_id = ?",
                (payload["groupId"],),
            ).fetchone()
            seq = int(row["n"] if row is not None else 0) + 1
            conn.execute(
                """
                INSERT INTO group_studio_events (
                    id, group_id, run_id, event_type, status, payload, created_at, sort_order
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    payload["groupId"],
                    payload.get("runId") or "",
                    payload["type"],
                    payload.get("status") or "",
                    self.dumps(payload.get("payload") or {}),
                    now,
                    seq,
                ),
            )
        return {
            "seq": seq,
            "groupId": payload["groupId"],
            "runId": payload.get("runId") or None,
            "type": payload["type"],
            "status": payload.get("status") or "",
            "payload": payload.get("payload") or {},
            "createdAt": now,
        }

    def replace_blackboard_payload(self, group_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.ensure_db()
        now = self.utc_now()
        with self.lock, self.connect() as conn:
            row = conn.execute(
                "SELECT revision FROM group_blackboards WHERE group_id = ?",
                (group_id,),
            ).fetchone()
            if row is None:
                return {}
            revision = int(row["revision"] or 1) + 1
            conn.execute(
                """
                UPDATE group_blackboards
                SET revision = ?, payload_json = ?, updated_at = ?
                WHERE group_id = ?
                """,
                (revision, self.dumps(payload), now, group_id),
            )
        snapshot = self.get_snapshot(group_id) or {}
        return snapshot.get("blackboard") or {}

    def list_artifacts(self, group_id: str) -> list[dict[str, Any]]:
        self.ensure_db()
        with self.lock, self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM group_artifacts
                WHERE group_id = ?
                ORDER BY created_at ASC
                """,
                (group_id,),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "groupId": row["group_id"],
                "title": row["title"],
                "path": row["path"],
                "mediaType": row["media_type"] or "",
                "byteSize": int(row["byte_size"] or 0),
                "createdBy": row["created_by"] or "",
                "createdAt": row["created_at"],
            }
            for row in rows
        ]

    def insert_artifact(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.ensure_db()
        now = payload.get("createdAt") or self.utc_now()
        artifact_id = payload.get("id") or f"gart_{now}"
        with self.lock, self.connect() as conn:
            conn.execute(
                """
                INSERT INTO group_artifacts (
                    id, group_id, title, path, media_type, byte_size, created_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact_id,
                    payload["groupId"],
                    payload.get("title") or payload.get("path") or "file",
                    payload["path"],
                    payload.get("mediaType") or "",
                    int(payload.get("byteSize") or 0),
                    payload.get("createdBy") or "",
                    now,
                ),
            )
        return {
            "id": artifact_id,
            "groupId": payload["groupId"],
            "title": payload.get("title") or payload.get("path") or "file",
            "path": payload["path"],
            "mediaType": payload.get("mediaType") or "",
            "byteSize": int(payload.get("byteSize") or 0),
            "createdBy": payload.get("createdBy") or "",
            "createdAt": now,
        }

    def insert_approval(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.ensure_db()
        now = payload.get("createdAt") or self.utc_now()
        approval_id = payload.get("id") or f"gapr_{now}"
        with self.lock, self.connect() as conn:
            conn.execute(
                """
                INSERT INTO group_approvals (
                    id, group_id, run_id, member_id, reply_id, tool_calls_json,
                    arguments_preview, status, created_at, resolved_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval_id,
                    payload["groupId"],
                    payload["runId"],
                    payload["memberId"],
                    payload.get("replyId") or "",
                    self.dumps(payload.get("toolCalls") or []),
                    payload.get("argumentsPreview") or "",
                    payload.get("status") or "pending",
                    now,
                    None,
                ),
            )
        row = self.get_approval(approval_id)
        return row or {"id": approval_id}

    def get_approval(self, approval_id: str) -> dict[str, Any] | None:
        self.ensure_db()
        with self.lock, self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM group_approvals WHERE id = ?",
                (approval_id,),
            ).fetchone()
        return self._approval_from_row(row)

    def get_pending_approval(self, group_id: str, run_id: str | None = None) -> dict[str, Any] | None:
        self.ensure_db()
        with self.lock, self.connect() as conn:
            if run_id:
                row = conn.execute(
                    """
                    SELECT * FROM group_approvals
                    WHERE group_id = ? AND run_id = ? AND status = 'pending'
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (group_id, run_id),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT * FROM group_approvals
                    WHERE group_id = ? AND status = 'pending'
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (group_id,),
                ).fetchone()
        return self._approval_from_row(row)

    def update_approval_status(self, approval_id: str, status: str) -> dict[str, Any] | None:
        self.ensure_db()
        now = self.utc_now()
        with self.lock, self.connect() as conn:
            conn.execute(
                """
                UPDATE group_approvals
                SET status = ?, resolved_at = ?, created_at = created_at
                WHERE id = ?
                """,
                (status, now, approval_id),
            )
        return self.get_approval(approval_id)

    def list_events_after(self, group_id: str, after_seq: int) -> list[dict[str, Any]]:
        self.ensure_db()
        with self.lock, self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM group_studio_events
                WHERE group_id = ? AND sort_order > ?
                ORDER BY sort_order ASC
                """,
                (group_id, after_seq),
            ).fetchall()
        events: list[dict[str, Any]] = []
        for row in rows:
            events.append({
                "seq": int(row["sort_order"]),
                "groupId": row["group_id"],
                "runId": row["run_id"] or None,
                "type": row["event_type"],
                "status": row["status"] or "",
                "payload": self.loads(row["payload"], {}),
                "createdAt": row["created_at"],
            })
        return events

    def apply_task_card_update(
        self,
        group_id: str,
        *,
        goal: str,
        background: str,
        constraints: list[str],
        dod_checklist: list[dict[str, Any]],
        card_status: str,
        studio_status: str,
        awaiting_restatement: bool,
        phase_before_pause: str | None,
        bump_revision: bool,
        title: str | None = None,
    ) -> dict[str, Any]:
        """更新当前任务卡、工作室阶段，并投影 Goal/约束到黑板。"""

        self.ensure_db()
        now = self.utc_now()
        with self.lock, self.connect() as conn:
            studio = conn.execute(
                "SELECT current_task_card_id FROM group_studios WHERE id = ?",
                (group_id,),
            ).fetchone()
            if studio is None:
                return {}
            card_id = str(studio["current_task_card_id"])
            current = conn.execute(
                "SELECT revision FROM group_task_cards WHERE id = ?",
                (card_id,),
            ).fetchone()
            revision = int(current["revision"] if current is not None else 1)
            if bump_revision:
                revision += 1
            conn.execute(
                """
                UPDATE group_task_cards
                SET revision = ?, status = ?, goal = ?, background = ?,
                    constraints_json = ?, dod_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    revision,
                    card_status,
                    goal,
                    background,
                    self.dumps(constraints),
                    self.dumps(dod_checklist),
                    now,
                    card_id,
                ),
            )
            conn.execute(
                """
                UPDATE group_studios
                SET status = ?, awaiting_restatement = ?, phase_before_pause = ?,
                    title = COALESCE(?, title), updated_at = ?
                WHERE id = ?
                """,
                (
                    studio_status,
                    1 if awaiting_restatement else 0,
                    phase_before_pause,
                    title,
                    now,
                    group_id,
                ),
            )
            blackboard = conn.execute(
                "SELECT revision, payload_json FROM group_blackboards WHERE group_id = ?",
                (group_id,),
            ).fetchone()
            payload = self.loads(blackboard["payload_json"], {}) if blackboard else {}
            if not isinstance(payload, dict):
                payload = {}
            payload["goal"] = goal
            payload["constraints"] = constraints
            payload["taskCardId"] = card_id
            board_revision = int(blackboard["revision"] if blackboard else 1) + (1 if bump_revision else 0)
            conn.execute(
                """
                UPDATE group_blackboards
                SET revision = ?, payload_json = ?, updated_at = ?
                WHERE group_id = ?
                """,
                (board_revision, self.dumps(payload), now, group_id),
            )
        return self.get_snapshot(group_id) or {}

    def list_with_current_run(self) -> list[dict[str, Any]]:
        """启动对账：仍挂着 current_run_id 的未终态工作室。"""

        self.ensure_db()
        with self.lock, self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, status, current_run_id, workspace_id, project_id,
                       chat_session_id, title
                FROM group_studios
                WHERE current_run_id != ''
                  AND status NOT IN ('archived', 'failed')
                """,
            ).fetchall()
        return [
            {
                "id": str(row["id"]),
                "status": str(row["status"]),
                "currentRunId": str(row["current_run_id"] or ""),
                "workspaceId": str(row["workspace_id"]),
                "projectId": str(row["project_id"]),
                "chatSessionId": str(row["chat_session_id"]),
                "title": str(row["title"]),
            }
            for row in rows
        ]

    def archive(self, group_id: str) -> None:
        """测试与删除会话用：把工作室标为 archived。"""

        self.ensure_db()
        now = self.utc_now()
        with self.lock, self.connect() as conn:
            conn.execute(
                """
                UPDATE group_studios
                SET status = 'archived', archived_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (now, now, group_id),
            )
            conn.execute(
                """
                UPDATE group_task_cards
                SET status = 'archived', updated_at = ?
                WHERE group_id = ? AND status IN ('draft', 'active', 'frozen_for_close')
                """,
                (now, group_id),
            )

    def list_messages(
        self,
        group_id: str,
        *,
        before: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """更早一页消息，按 sort_order 升序。"""

        self.ensure_db()
        limit = max(1, min(int(limit or 100), 200))
        with self.lock, self.connect() as conn:
            if before:
                row = conn.execute(
                    "SELECT sort_order FROM group_messages WHERE id = ? AND group_id = ?",
                    (before, group_id),
                ).fetchone()
                before_order = int(row["sort_order"]) if row is not None else 0
                rows = conn.execute(
                    """
                    SELECT * FROM group_messages
                    WHERE group_id = ? AND sort_order < ?
                    ORDER BY sort_order DESC
                    LIMIT ?
                    """,
                    (group_id, before_order, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM group_messages
                    WHERE group_id = ?
                    ORDER BY sort_order DESC
                    LIMIT ?
                    """,
                    (group_id, limit),
                ).fetchall()
        return [self._message_from_row(row) for row in reversed(rows)]

    def _insert_new_studio(
        self,
        conn: sqlite3.Connection,
        payload: dict[str, Any],
        now: str,
    ) -> None:
        group_id = payload["id"]
        task = payload["taskCard"]
        members = list(payload.get("members") or [])
        artifacts = list(payload.get("artifacts") or [])
        conn.execute(
            """
            INSERT INTO group_studios (
                id, workspace_id, project_id, chat_session_id, title, status,
                phase_before_pause, current_task_card_id, awaiting_restatement,
                orchestrator_meta, budget_json, current_run_id,
                pending_speaker_member_id,
                created_at, updated_at, archived_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                group_id,
                payload["workspaceId"],
                payload["projectId"],
                payload["chatSessionId"],
                payload["title"],
                payload["status"],
                None,
                task["id"],
                0,
                self.dumps(payload.get("orchestratorMeta") or {}),
                self.dumps(payload["budget"]),
                "",
                "",
                now,
                now,
                None,
            ),
        )
        conn.execute(
            """
            INSERT INTO group_task_cards (
                id, group_id, revision, status, goal, background,
                constraints_json, dod_json, seed_from_chat, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task["id"],
                group_id,
                int(task.get("revision") or 1),
                task["status"],
                task.get("goal") or "",
                task.get("background") or "",
                self.dumps(task.get("constraints") or []),
                self.dumps(task.get("dodChecklist") or []),
                1 if task.get("seedFromChat") else 0,
                now,
                now,
            ),
        )
        blackboard = payload["blackboard"]
        conn.execute(
            """
            INSERT INTO group_blackboards (group_id, revision, payload_json, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                group_id,
                int(blackboard.get("revision") or 1),
                self.dumps({
                    "taskCardId": task["id"],
                    "goal": blackboard.get("goal") or task.get("goal") or "",
                    "constraints": blackboard.get("constraints") or task.get("constraints") or [],
                    "decisions": blackboard.get("decisions") or [],
                    "openQuestions": blackboard.get("openQuestions") or [],
                    "artifacts": blackboard.get("artifacts") or [],
                }),
                now,
            ),
        )
        for member in members:
            conn.execute(
                """
                INSERT INTO group_memberships (
                    id, group_id, role, agent_id, display_name, status, muted,
                    last_spoke_at, crash_count, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    member["id"],
                    group_id,
                    member["role"],
                    member["agentId"],
                    member["displayName"],
                    member.get("status") or "active",
                    1 if member.get("muted") else 0,
                    None,
                    int(member.get("crashCount") or 0),
                    now,
                    now,
                ),
            )
        for artifact in artifacts:
            conn.execute(
                """
                INSERT INTO group_artifacts (
                    id, group_id, title, path, media_type, byte_size, created_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact["id"],
                    group_id,
                    artifact.get("title") or artifact.get("path") or "file",
                    artifact["path"],
                    artifact.get("mediaType") or "",
                    int(artifact.get("byteSize") or 0),
                    artifact.get("createdBy") or "",
                    now,
                ),
            )

    def _task_card_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "groupId": row["group_id"],
            "revision": int(row["revision"]),
            "status": row["status"],
            "goal": row["goal"] or "",
            "background": row["background"] or "",
            "constraints": self.loads(row["constraints_json"], []),
            "dodChecklist": self.loads(row["dod_json"], []),
            "seedFromChat": bool(row["seed_from_chat"]),
            "updatedAt": row["updated_at"],
        }

    def _blackboard_from_row(self, row: sqlite3.Row, task_card_id: str) -> dict[str, Any]:
        payload = self.loads(row["payload_json"], {})
        if not isinstance(payload, dict):
            payload = {}
        return {
            "groupId": row["group_id"],
            "revision": int(row["revision"]),
            "taskCardId": str(payload.get("taskCardId") or task_card_id),
            "goal": str(payload.get("goal") or ""),
            "constraints": list(payload.get("constraints") or []),
            "decisions": list(payload.get("decisions") or []),
            "openQuestions": list(payload.get("openQuestions") or []),
            "artifacts": list(payload.get("artifacts") or []),
            "updatedAt": row["updated_at"],
        }

    def _member_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "groupId": row["group_id"],
            "role": row["role"],
            "agentId": row["agent_id"],
            "displayName": row["display_name"],
            "status": row["status"],
            "muted": bool(row["muted"]),
            "lastSpokeAt": row["last_spoke_at"] or None,
            "crashCount": int(row["crash_count"] or 0),
        }

    def _message_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "groupId": row["group_id"],
            "runId": row["run_id"] or None,
            "speakerId": row["speaker_id"],
            "role": row["role"],
            "intent": row["intent"],
            "body": row["body"] or "",
            "thinking": row["thinking"] if "thinking" in row.keys() else "",
            "mentions": self.loads(row["mentions_json"], []),
            "attachments": self.loads(row["attachments_json"], []),
            "visibleInTimeline": bool(row["visible_in_timeline"]),
            "createdAt": row["created_at"],
        }

    def _approval_from_row(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        tool_calls = self.loads(row["tool_calls_json"], [])
        if not isinstance(tool_calls, list):
            tool_calls = []
        return {
            "id": row["id"],
            "groupId": row["group_id"],
            "runId": row["run_id"],
            "memberId": row["member_id"],
            "replyId": row["reply_id"],
            "toolCalls": tool_calls,
            "toolNames": [
                str(item.get("name") or item.get("toolName") or "")
                for item in tool_calls
                if isinstance(item, dict)
            ],
            "argumentsPreview": row["arguments_preview"] or "",
            "status": row["status"],
            "createdAt": row["created_at"],
        }
