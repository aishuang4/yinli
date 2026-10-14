"""聊天会话与消息的 SQLite 读写。

只负责 chat_sessions / chat_messages，不决定注入或展示。
增量方法按行写入；load_store / save_store 会在调用方迁完后删除。
"""

from __future__ import annotations

import ast
import logging
import re
import sqlite3
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

# json.dumps(default=str) 曾把 Pydantic 模型存成 `id='...' name='...'`。
_MODEL_BLOB = re.compile(
    r"(\w+)=("
    r"'(?:\\'|[^'])*'"
    r"|\"(?:\\\"|[^\"])*\""
    r"|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?"
    r"|None|True|False"
    r")"
)


def _parse_stringified_model(value: str) -> dict[str, Any] | None:
    """把旧的模型字面量还原成字段字典。不像模型字面量就原样放弃。"""

    if "=" not in value:
        return None
    fields: dict[str, Any] = {}
    pos = 0
    length = len(value)
    while pos < length:
        if value[pos].isspace():
            pos += 1
            continue
        match = _MODEL_BLOB.match(value, pos)
        if match is None:
            return None
        try:
            fields[match.group(1)] = ast.literal_eval(match.group(2))
        except (SyntaxError, ValueError):
            return None
        pos = match.end()
    return fields or None


def _coerce_loaded(value: Any) -> Any:
    if isinstance(value, list):
        return [_coerce_loaded(item) for item in value]
    if isinstance(value, str):
        parsed = _parse_stringified_model(value)
        if parsed is not None:
            return parsed
    return value

_SESSION_UPDATE_FIELDS = {
    "title": "title",
    "agent_id": "agent_id",
    "agent_name": "agent_name",
    "mode": "mode",
    "workspace_id": "workspace_id",
    "project_id": "project_id",
    "model_config_id": "model_config_id",
    "updated_at": "updated_at",
    "message_count": "message_count",
}


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

    def get_location(self, session_id: str) -> dict[str, str] | None:
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
            "toolCalls": _coerce_loaded(self.loads(assistant_row["tool_calls"], [])),
            "runtimeEvents": _coerce_loaded(self.loads(assistant_row["runtime_events"], [])),
            "createdAt": assistant_row["created_at"],
        }

    def _load_json(self, raw: str | None, default: Any, *, row_id: str, column: str) -> Any:
        try:
            return _coerce_loaded(self.loads(raw, default))
        except (TypeError, ValueError):
            logger.warning("聊天记录 JSON 损坏 id=%s column=%s", row_id, column)
            return default

    def _session_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        keys = row.keys()
        return {
            "id": row["id"],
            "title": row["title"],
            "agent_id": row["agent_id"],
            "agent_name": row["agent_name"],
            "mode": row["mode"] or "agent",
            "workspace_id": row["workspace_id"] or self.default_workspace_id,
            "project_id": row["project_id"] or self.default_project_id,
            "kind": row["kind"] if "kind" in keys and row["kind"] else "chat",
            "modelConfigId": row["model_config_id"] or "",
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "message_count": row["message_count"],
        }

    def _message_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        keys = row.keys()
        message_id = str(row["id"])

        def column(name: str, default: Any) -> Any:
            if name not in keys:
                return default
            return self._load_json(row[name], default, row_id=message_id, column=name)

        return {
            "id": message_id,
            "session_id": row["session_id"],
            "role": row["role"],
            "content": row["content"] or "",
            "agent_id": row["agent_id"],
            "agent_name": row["agent_name"],
            "model_name": row["model_name"],
            "error": row["error"],
            "thinking": row["thinking"] or "",
            "images": column("images", []),
            "tool_calls": column("tool_calls", []),
            "skill_calls": column("skill_calls", []),
            "model_caches": column("model_caches", []),
            "runtime_events": column("runtime_events", []),
            "plan_runs": column("plan_runs", []),
            "displaySnapshot": column("display_snapshot", self.empty_display_snapshot),
            "files": column("files", []),
            "clarification_requests": column("clarification_requests", []),
            "clarification_resolved": bool(row["clarification_resolved"]) if "clarification_resolved" in keys else False,
            "created_at": row["created_at"],
            "sort_order": row["sort_order"],
        }

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        self.ensure_db()
        with self.lock, self.connect() as conn:
            row = conn.execute("SELECT * FROM chat_sessions WHERE id = ?", (session_id,)).fetchone()
        return self._session_from_row(row) if row else None

    def list_sessions(self, *, project_id: str | None = None, kind: str | None = None) -> list[dict[str, Any]]:
        self.ensure_db()
        clauses: list[str] = []
        params: list[Any] = []
        if project_id is not None:
            clauses.append("project_id = ?")
            params.append(project_id)
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.lock, self.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM chat_sessions {where} ORDER BY updated_at DESC",
                params,
            ).fetchall()
        return [self._session_from_row(row) for row in rows]

    def insert_session_if_absent(self, session: dict[str, Any]) -> dict[str, Any]:
        """INSERT OR IGNORE 后返回库里的那一行（可能是并发先插入的）。"""

        self.ensure_db()
        with self.lock, self.connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO chat_sessions (
                    id, title, agent_id, agent_name, mode, workspace_id, project_id, kind,
                    created_at, updated_at, message_count, model_config_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session["id"],
                    session.get("title") or "新对话",
                    session.get("agent_id") or session.get("agentId") or "",
                    session.get("agent_name") or session.get("agentName") or "",
                    session.get("mode") or "agent",
                    session.get("workspace_id") or session.get("workspaceId") or self.default_workspace_id,
                    session.get("project_id") or session.get("projectId") or self.default_project_id,
                    session.get("kind") or "chat",
                    session.get("created_at") or session.get("createdAt") or "",
                    session.get("updated_at") or session.get("updatedAt") or "",
                    int(session.get("message_count") or session.get("messageCount") or 0),
                    session.get("modelConfigId") or session.get("model_config_id") or "",
                ),
            )
            row = conn.execute("SELECT * FROM chat_sessions WHERE id = ?", (session["id"],)).fetchone()
        return self._session_from_row(row)

    def update_session(self, session_id: str, **fields: Any) -> None:
        assignments: list[str] = []
        params: list[Any] = []
        for key, column in _SESSION_UPDATE_FIELDS.items():
            if key not in fields:
                continue
            assignments.append(f"{column} = ?")
            params.append(fields[key])
        if not assignments:
            return
        params.append(session_id)
        self.ensure_db()
        with self.lock, self.connect() as conn:
            conn.execute(
                f"UPDATE chat_sessions SET {', '.join(assignments)} WHERE id = ?",
                params,
            )

    def append_message(self, session_id: str, message: dict[str, Any]) -> dict[str, Any]:
        # 消息外键指向会话。审计或重启收口可能先写消息，这时补一条占位会话。
        self.insert_session_if_absent({
            "id": session_id,
            "title": "未命名会话",
            "agent_id": message.get("agent_id") or message.get("agentId") or "",
            "agent_name": message.get("agent_name") or message.get("agentName") or "",
            "created_at": message.get("created_at") or message.get("createdAt") or "",
            "updated_at": message.get("created_at") or message.get("createdAt") or "",
        })
        self.ensure_db()
        with self.lock, self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            sort_order = conn.execute(
                "SELECT COALESCE(MAX(sort_order), -1) + 1 AS n FROM chat_messages WHERE session_id = ?",
                (session_id,),
            ).fetchone()["n"]
            conn.execute(
                """
                INSERT INTO chat_messages (
                    id, session_id, role, content, agent_id, agent_name, model_name,
                    error, thinking, images, tool_calls, skill_calls,
                    model_caches, runtime_events, plan_runs,
                    display_snapshot, files, clarification_requests, clarification_resolved,
                    created_at, sort_order
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message["id"],
                    session_id,
                    message.get("role") or "user",
                    message.get("content") or "",
                    message.get("agent_id") or message.get("agentId"),
                    message.get("agent_name") or message.get("agentName"),
                    message.get("model_name") or message.get("modelName"),
                    message.get("error"),
                    message.get("thinking") or "",
                    self.dumps(message.get("images") or []),
                    self.dumps(message.get("tool_calls") or message.get("toolCalls") or []),
                    self.dumps(message.get("skill_calls") or message.get("skillCalls") or []),
                    self.dumps(message.get("model_caches") or message.get("modelCaches") or []),
                    self.dumps(message.get("runtime_events") or message.get("runtimeEvents") or []),
                    self.dumps(message.get("plan_runs") or message.get("planRuns") or []),
                    self.dumps(message.get("displaySnapshot") or message.get("display_snapshot") or self.empty_display_snapshot),
                    self.dumps(message.get("files") or []),
                    self.dumps(message.get("clarification_requests") or message.get("clarificationRequests") or []),
                    1 if message.get("clarification_resolved") or message.get("clarificationResolved") else 0,
                    message.get("created_at") or message.get("createdAt") or "",
                    sort_order,
                ),
            )
            conn.execute(
                """
                UPDATE chat_sessions
                SET message_count = (SELECT COUNT(*) FROM chat_messages WHERE session_id = ?),
                    updated_at = ?
                WHERE id = ?
                """,
                (session_id, message.get("updated_at") or message.get("created_at") or message.get("createdAt") or "", session_id),
            )
            row = conn.execute("SELECT * FROM chat_messages WHERE id = ?", (message["id"],)).fetchone()
            conn.commit()
        return self._message_from_row(row)

    def list_messages(self, session_id: str) -> list[dict[str, Any]]:
        self.ensure_db()
        with self.lock, self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM chat_messages WHERE session_id = ? ORDER BY sort_order",
                (session_id,),
            ).fetchall()
        return [self._message_from_row(row) for row in rows]

    def update_message_snapshot(self, message_id: str, snapshot: dict[str, Any]) -> None:
        self.ensure_db()
        with self.lock, self.connect() as conn:
            conn.execute(
                "UPDATE chat_messages SET display_snapshot = ? WHERE id = ?",
                (self.dumps(snapshot), message_id),
            )

    def delete_messages(self, session_id: str, message_ids: list[str]) -> None:
        if not message_ids:
            return
        self.ensure_db()
        marks = ",".join("?" for _ in message_ids)
        with self.lock, self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                f"DELETE FROM chat_messages WHERE session_id = ? AND id IN ({marks})",
                (session_id, *message_ids),
            )
            conn.execute(
                """
                UPDATE chat_sessions
                SET message_count = (SELECT COUNT(*) FROM chat_messages WHERE session_id = ?)
                WHERE id = ?
                """,
                (session_id, session_id),
            )
            conn.commit()

    def delete_session(self, session_id: str) -> None:
        self.ensure_db()
        with self.lock, self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM chat_messages WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM chat_sessions WHERE id = ?", (session_id,))
            conn.commit()
