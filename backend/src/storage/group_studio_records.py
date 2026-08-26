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
                created_at, updated_at, archived_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
