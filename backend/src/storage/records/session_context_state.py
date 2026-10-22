"""会话级 AgentScope 上下文状态（压缩摘要 + middleware 跨轮数据）的 SQLite 领域存储。

只保存 `state.summary` 和 `state.middle_context` 中可 JSON 化的部分；对话正文仍由
`chat_messages` 重建。写入在每轮成功 / 失败收尾时做，读取只在进程内没有实例上下文、
需要重建时做。
"""
from __future__ import annotations


import json
import sqlite3
from collections.abc import Callable
from typing import Any

# middle_context 里这些 key 只在单轮内有意义，不落库。
TRANSIENT_MIDDLE_CONTEXT_KEYS = frozenset({"aw_activated_skills_pending_check"})
# 单个会话落库上限，避免异常膨胀的 middle_context 拖慢 SQLite。
MIDDLE_CONTEXT_MAX_BYTES = 256 * 1024


class SessionContextStateStore:
    """封装会话上下文状态的读写。"""

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
    def _serializable_middle_context(middle_context: dict[str, Any] | None) -> str:
        payload: dict[str, Any] = {}
        for key, value in (middle_context or {}).items():
            if key in TRANSIENT_MIDDLE_CONTEXT_KEYS:
                continue
            try:
                json.dumps(value, ensure_ascii=False)
            except (TypeError, ValueError):
                continue
            payload[key] = value
        text = json.dumps(payload, ensure_ascii=False)
        if len(text.encode("utf-8")) > MIDDLE_CONTEXT_MAX_BYTES:
            return "{}"
        return text

    def save(self, *, session_id: str, agent_id: str, summary: str, middle_context: dict[str, Any] | None) -> None:
        if not session_id:
            return
        self.ensure_db()
        now = self.utc_now()
        with self.lock, self.connect() as conn:
            conn.execute(
                """
                INSERT INTO session_context_state (session_id, agent_id, summary, middle_context, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    agent_id = excluded.agent_id,
                    summary = excluded.summary,
                    middle_context = excluded.middle_context,
                    updated_at = excluded.updated_at
                """,
                (session_id, agent_id, summary or "", self._serializable_middle_context(middle_context), now),
            )

    def load(self, session_id: str) -> dict[str, Any] | None:
        if not session_id:
            return None
        self.ensure_db()
        with self.lock, self.connect() as conn:
            row = conn.execute(
                "SELECT session_id, agent_id, summary, middle_context, updated_at FROM session_context_state WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if not row:
            return None
        try:
            middle_context = json.loads(row["middle_context"] or "{}")
        except (TypeError, ValueError):
            middle_context = {}
        return {
            "sessionId": row["session_id"],
            "agentId": row["agent_id"],
            "summary": row["summary"] or "",
            "middleContext": middle_context if isinstance(middle_context, dict) else {},
            "updatedAt": row["updated_at"],
        }

    def delete(self, session_id: str) -> None:
        if not session_id:
            return
        self.ensure_db()
        with self.lock, self.connect() as conn:
            conn.execute("DELETE FROM session_context_state WHERE session_id = ?", (session_id,))

from src.storage.engine.database import _connect, _lock, _utc_now, ensure_db

def _session_context_state_store() -> SessionContextStateStore:
    """创建复用当前数据库基础设施的会话上下文状态存储。"""

    return SessionContextStateStore(
        ensure_db=ensure_db,
        connect=_connect,
        lock=_lock,
        utc_now=_utc_now,
    )

def save_session_context_state(
    *,
    session_id: str,
    agent_id: str,
    summary: str,
    middle_context: dict[str, Any] | None,
) -> None:
    _session_context_state_store().save(
        session_id=session_id,
        agent_id=agent_id,
        summary=summary,
        middle_context=middle_context,
    )

def load_session_context_state(session_id: str) -> dict[str, Any] | None:
    return _session_context_state_store().load(session_id)

def delete_session_context_state(session_id: str) -> None:
    _session_context_state_store().delete(session_id)
