"""聊天会话与消息的 SQLite 读写。

只负责 chat_sessions / chat_messages，不决定注入或展示。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any


class ChatSessionRecordStore:
    """封装会话归属、整库快照和最近一轮消息查询。"""

    def __init__(
        self,
        *,
        ensure_db: Callable[[], None],
        connect: Callable[[], sqlite3.Connection],
        lock: Any,
        dumps: Callable[[Any], str],
        loads: Callable[[str | None, Any], Any],
        empty_display_snapshot: dict[str, Any],
        default_workspace_id: str,
        default_project_id: str,
    ) -> None:
        self.ensure_db = ensure_db
        self.connect = connect
        self.lock = lock
        self.dumps = dumps
        self.loads = loads
        self.empty_display_snapshot = empty_display_snapshot
        self.default_workspace_id = default_workspace_id
        self.default_project_id = default_project_id

    def get_ownership(self, session_id: str) -> dict[str, str] | None:
        self.ensure_db()
        with self.lock, self.connect() as conn:
            row = conn.execute(
                "SELECT workspace_id, project_id FROM chat_sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "workspaceId": str(row["workspace_id"] or self.default_workspace_id),
            "projectId": str(row["project_id"] or self.default_project_id),
        }

    def load_store(self) -> dict[str, Any]:
        self.ensure_db()
        with self.lock, self.connect() as conn:
            sessions = conn.execute(
                "SELECT * FROM chat_sessions ORDER BY updated_at ASC"
            ).fetchall()
            messages = conn.execute(
                "SELECT * FROM chat_messages ORDER BY session_id, sort_order ASC"
            ).fetchall()
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in messages:
            grouped.setdefault(row["session_id"], []).append({
                "id": row["id"],
                "role": row["role"],
                "content": row["content"],
                "agent_id": row["agent_id"],
                "agent_name": row["agent_name"],
                "model_name": row["model_name"],
                "error": row["error"],
                "thinking": row["thinking"],
                "images": self.loads(row["images"], []),
                "displaySnapshot": self.loads(row["display_snapshot"], self.empty_display_snapshot),
                "created_at": row["created_at"],
            })
        return {
            "sessions": [
                {
                    "id": row["id"],
                    "title": row["title"],
                    "agent_id": row["agent_id"],
                    "agent_name": row["agent_name"],
                    "mode": row["mode"],
                    "workspace_id": row["workspace_id"],
                    "project_id": row["project_id"],
                    "kind": (row["kind"] if "kind" in row.keys() and row["kind"] else "chat"),
                    "modelConfigId": row["model_config_id"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                    "message_count": row["message_count"],
                    "ownerUserId": (
                        row["owner_user_id"] or ""
                        if "owner_user_id" in row.keys()
                        else ""
                    ),
                }
                for row in sessions
            ],
            "messages": grouped,
        }

    def save_store(self, data: dict[str, Any]) -> None:
        self.ensure_db()
        sessions = data.get("sessions", [])
        messages_by_session = data.get("messages", {})
        with self.lock, self.connect() as conn:
            conn.execute("DELETE FROM chat_messages")
            conn.execute("DELETE FROM chat_sessions")
            conn.executemany(
                """
                INSERT INTO chat_sessions (
                    id, title, agent_id, agent_name, mode, workspace_id, project_id, kind,
                    created_at, updated_at, message_count, model_config_id, owner_user_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        item["id"], item["title"], item["agent_id"], item["agent_name"],
                        item.get("mode", "agent"),
                        item.get("workspace_id", item.get("workspaceId", self.default_workspace_id)),
                        item.get("project_id", item.get("projectId", self.default_project_id)),
                        item.get("kind") or "chat",
                        item["created_at"],
                        item["updated_at"], item.get("message_count", 0),
                        item.get("modelConfigId", item.get("model_config_id", "")),
                        item.get("ownerUserId", item.get("owner_user_id", "")) or "",
                    )
                    for item in sessions
                ],
            )
            rows = []
            for session_id, messages in messages_by_session.items():
                for index, item in enumerate(messages):
                    rows.append((
                        item["id"], session_id, item["role"], item.get("content", ""),
                        item.get("agent_id"), item.get("agent_name"), item.get("model_name"),
                        item.get("error"), item.get("thinking", ""), self.dumps(item.get("images", [])),
                        self.dumps([]),
                        self.dumps([]),
                        self.dumps([]),
                        self.dumps([]),
                        self.dumps([]),
                        self.dumps(item.get("displaySnapshot", item.get("display_snapshot", self.empty_display_snapshot))),
                        item["created_at"], index,
                    ))
            conn.executemany(
                """
                INSERT INTO chat_messages (
                    id, session_id, role, content, agent_id, agent_name, model_name,
                    error, thinking, images, tool_calls, skill_calls,
                    model_caches, runtime_events, plan_runs,
                    display_snapshot, created_at,
                    sort_order
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.commit()

    def latest_assistant(self, session_id: str) -> dict[str, Any] | None:
        self.ensure_db()
        with self.lock, self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM chat_messages
                WHERE session_id = ? AND role = 'assistant'
                ORDER BY sort_order DESC, created_at DESC
                LIMIT 1
                """,
                (session_id,),
            ).fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "sessionId": row["session_id"],
            "role": row["role"],
            "content": row["content"] or "",
        }

    def latest_user(self, session_id: str, content: str | None = None) -> dict[str, Any] | None:
        self.ensure_db()
        with self.lock, self.connect() as conn:
            if content is None:
                row = conn.execute(
                    """
                    SELECT * FROM chat_messages
                    WHERE session_id = ? AND role = 'user'
                    ORDER BY sort_order DESC, created_at DESC
                    LIMIT 1
                    """,
                    (session_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT * FROM chat_messages
                    WHERE session_id = ? AND role = 'user' AND content = ?
                    ORDER BY sort_order DESC, created_at DESC
                    LIMIT 1
                    """,
                    (session_id, content),
                ).fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "sessionId": row["session_id"],
            "workspaceId": row["workspace_id"],
            "projectId": row["project_id"],
            "role": row["role"],
            "content": row["content"],
            "agentId": row["agent_id"],
            "agentName": row["agent_name"],
            "modelName": row["model_name"],
            "images": self.loads(row["images"], []),
            "createdAt": row["created_at"],
        }

    def latest_assistant_preview(self, session_id: str) -> dict[str, Any] | None:
        self.ensure_db()
        with self.lock, self.connect() as conn:
            row = conn.execute(
                """
                SELECT id, content, agent_name, model_name, created_at
                FROM chat_messages
                WHERE session_id = ?
                  AND role = 'assistant'
                  AND length(trim(content)) > 0
                ORDER BY sort_order DESC, created_at DESC
                LIMIT 1
                """,
                (session_id,),
            ).fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "content": row["content"],
            "agentName": row["agent_name"],
            "modelName": row["model_name"],
            "createdAt": row["created_at"],
        }

    def latest_completed_turn(
        self,
        session_id: str,
        *,
        before_user_content: str | None = None,
    ) -> dict[str, Any] | None:
        self.ensure_db()
        with self.lock, self.connect() as conn:
            before_sort_order: int | None = None
            if before_user_content is not None:
                current_user = conn.execute(
                    """
                    SELECT sort_order FROM chat_messages
                    WHERE session_id = ? AND role = 'user' AND content = ?
                    ORDER BY sort_order DESC, created_at DESC
                    LIMIT 1
                    """,
                    (session_id, before_user_content),
                ).fetchone()
                if current_user:
                    before_sort_order = int(current_user["sort_order"])

            if before_sort_order is None:
                assistant_row = conn.execute(
                    """
                    SELECT * FROM chat_messages
                    WHERE session_id = ?
                      AND role = 'assistant'
                      AND length(trim(content)) > 0
                    ORDER BY sort_order DESC, created_at DESC
                    LIMIT 1
                    """,
                    (session_id,),
                ).fetchone()
            else:
                assistant_row = conn.execute(
                    """
                    SELECT * FROM chat_messages
                    WHERE session_id = ?
                      AND role = 'assistant'
                      AND length(trim(content)) > 0
                      AND sort_order < ?
                    ORDER BY sort_order DESC, created_at DESC
                    LIMIT 1
                    """,
                    (session_id, before_sort_order),
                ).fetchone()

            if not assistant_row:
                return None

            user_row = conn.execute(
                """
                SELECT * FROM chat_messages
                WHERE session_id = ?
                  AND role = 'user'
                  AND sort_order < ?
                ORDER BY sort_order DESC, created_at DESC
                LIMIT 1
                """,
                (session_id, int(assistant_row["sort_order"])),
            ).fetchone()

        if not user_row:
            return None
        return {
            "userMessageId": user_row["id"],
            "assistantMessageId": assistant_row["id"],
            "userMessage": user_row["content"],
            "assistantText": assistant_row["content"],
            "toolCalls": self.loads(assistant_row["tool_calls"], []),
            "runtimeEvents": self.loads(assistant_row["runtime_events"], []),
            "createdAt": assistant_row["created_at"],
        }
