"""Domain Event schema 与 Run 内全局 ordinal 持久化仓库。"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DomainEventDraft(BaseModel):
    """领域服务提交的不可变事实，不包含由仓库分配的 ordinal。"""

    model_config = ConfigDict(extra="forbid")

    type: str
    aggregate_type: str
    aggregate_id: str
    revision: int = Field(ge=1)
    data: dict[str, Any] = Field(default_factory=dict)
    data_version: int = Field(default=1, ge=1)
    id: str = Field(default_factory=lambda: f"domain-{uuid4().hex}")
    occurred_at: str = Field(default_factory=_utc_now)
    causation_id: str | None = None
    correlation_id: str | None = None
    trace_id: str | None = None


class DomainEvent(DomainEventDraft):
    """已持久化并具有 Run 内严格顺序的 Domain Event。"""

    stream_key: str
    stream_ordinal: int = Field(ge=1)
    run_id: str


class DomainEventRepository:
    """在 SQLite transaction 内分配 stream_ordinal 并 append Domain Event。"""

    def __init__(self, *, ensure_db=None, connect=None, lock=None) -> None:
        if ensure_db is None or connect is None or lock is None:
            from src.storage.engine import database

            ensure_db = ensure_db or database.ensure_db
            connect = connect or database._connect
            lock = lock or database._lock
        self.ensure_db = ensure_db
        self.connect = connect
        self.lock = lock

    def ensure_stream(self, stream_key: str, run_id: str) -> None:
        """幂等创建领域流；同一 stream_key 不得换绑 Run。"""

        self.ensure_db()
        now = _utc_now()
        with self.lock, self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._ensure_stream_in_connection(conn, stream_key, run_id, now)
            conn.commit()

    def append(self, stream_key: str, run_id: str, draft: DomainEventDraft) -> DomainEvent:
        """独立事务 append；领域 aggregate 可改用 append_in_transaction 原子提交。"""

        self.ensure_db()
        with self.lock, self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            event = self.append_in_transaction(conn, stream_key, run_id, draft)
            conn.commit()
        return event

    def append_in_transaction(
        self,
        conn: sqlite3.Connection,
        stream_key: str,
        run_id: str,
        draft: DomainEventDraft,
    ) -> DomainEvent:
        """复用调用方 transaction，使 aggregate 更新与事实 append 原子提交。"""

        now = _utc_now()
        self._ensure_stream_in_connection(conn, stream_key, run_id, now)
        row = conn.execute(
            "SELECT next_ordinal FROM chat_domain_streams WHERE stream_key = ?",
            (stream_key,),
        ).fetchone()
        if row is None:
            raise RuntimeError(f"Domain stream 不存在: {stream_key}")
        ordinal = int(row["next_ordinal"])
        event = DomainEvent(
            **draft.model_dump(),
            stream_key=stream_key,
            stream_ordinal=ordinal,
            run_id=run_id,
        )
        conn.execute(
            """
            INSERT INTO chat_domain_events (
                id, stream_key, stream_ordinal, run_id, aggregate_type, aggregate_id,
                type, data_version, revision, data_json, occurred_at,
                causation_id, correlation_id, trace_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.id,
                stream_key,
                ordinal,
                run_id,
                event.aggregate_type,
                event.aggregate_id,
                event.type,
                event.data_version,
                event.revision,
                json.dumps(event.data, ensure_ascii=False, separators=(",", ":")),
                event.occurred_at,
                event.causation_id,
                event.correlation_id,
                event.trace_id,
            ),
        )
        conn.execute(
            "UPDATE chat_domain_streams SET next_ordinal = ?, updated_at = ? WHERE stream_key = ?",
            (ordinal + 1, now, stream_key),
        )
        return event

    def get_next(self, stream_key: str, after_ordinal: int) -> DomainEvent | None:
        """只读取 checkpoint 后紧邻的一条事实，禁止跨越缺口。"""

        self.ensure_db()
        with self.lock, self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM chat_domain_events
                WHERE stream_key = ? AND stream_ordinal = ?
                """,
                (stream_key, after_ordinal + 1),
            ).fetchone()
        return self._from_row(row) if row is not None else None

    def list(self, stream_key: str, *, after_ordinal: int = 0) -> list[DomainEvent]:
        """按全局 ordinal 返回完整或尾部领域事实。"""

        self.ensure_db()
        with self.lock, self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM chat_domain_events
                WHERE stream_key = ? AND stream_ordinal > ?
                ORDER BY stream_ordinal ASC
                """,
                (stream_key, after_ordinal),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    @staticmethod
    def _ensure_stream_in_connection(
        conn: sqlite3.Connection,
        stream_key: str,
        run_id: str,
        now: str,
    ) -> None:
        conn.execute(
            """
            INSERT OR IGNORE INTO chat_domain_streams (
                stream_key, run_id, next_ordinal, created_at, updated_at
            ) VALUES (?, ?, 1, ?, ?)
            """,
            (stream_key, run_id, now, now),
        )
        row = conn.execute(
            "SELECT run_id FROM chat_domain_streams WHERE stream_key = ?",
            (stream_key,),
        ).fetchone()
        if row is None or str(row["run_id"]) != run_id:
            raise ValueError(f"stream_key 已绑定其他 Run: {stream_key}")

    @staticmethod
    def _from_row(row: sqlite3.Row) -> DomainEvent:
        data = json.loads(str(row["data_json"]))
        return DomainEvent(
            id=str(row["id"]),
            stream_key=str(row["stream_key"]),
            stream_ordinal=int(row["stream_ordinal"]),
            run_id=str(row["run_id"]),
            aggregate_type=str(row["aggregate_type"]),
            aggregate_id=str(row["aggregate_id"]),
            type=str(row["type"]),
            data_version=int(row["data_version"]),
            revision=int(row["revision"]),
            data=data if isinstance(data, dict) else {},
            occurred_at=str(row["occurred_at"]),
            causation_id=row["causation_id"],
            correlation_id=row["correlation_id"],
            trace_id=row["trace_id"],
        )
