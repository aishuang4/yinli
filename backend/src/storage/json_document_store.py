"""轻量 JSON Namespace 文档的 SQLite 存储。"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from typing import Any


class JsonDocumentStore:
    """封装 json_store 的 CRUD。"""

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

    def list(self, namespace: str) -> list[dict[str, Any]]:
        self.ensure_db()
        with self.lock, self.connect() as conn:
            rows = conn.execute(
                "SELECT value FROM json_store WHERE namespace = ? ORDER BY updated_at ASC",
                (namespace,),
            ).fetchall()
        return [json.loads(row["value"]) for row in rows]

    def get(self, namespace: str, key: str) -> dict[str, Any] | None:
        self.ensure_db()
        with self.lock, self.connect() as conn:
            row = conn.execute(
                "SELECT value FROM json_store WHERE namespace = ? AND key = ?",
                (namespace, key),
            ).fetchone()
        return json.loads(row["value"]) if row else None

    def upsert(self, namespace: str, key: str, value: dict[str, Any]) -> None:
        self.ensure_db()
        now = self.utc_now()
        payload = json.dumps(value, ensure_ascii=False, default=str)
        with self.lock, self.connect() as conn:
            conn.execute(
                """
                INSERT INTO json_store(namespace, key, value, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(namespace, key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (namespace, key, payload, now, now),
            )
            conn.commit()

    def replace_namespace(self, namespace: str, records: dict[str, dict[str, Any]]) -> None:
        self.ensure_db()
        now = self.utc_now()
        with self.lock, self.connect() as conn:
            conn.execute("DELETE FROM json_store WHERE namespace = ?", (namespace,))
            conn.executemany(
                """
                INSERT INTO json_store(namespace, key, value, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (namespace, key, json.dumps(value, ensure_ascii=False, default=str), now, now)
                    for key, value in records.items()
                ],
            )
            conn.commit()

    def delete(self, namespace: str, key: str) -> None:
        self.ensure_db()
        with self.lock, self.connect() as conn:
            conn.execute(
                "DELETE FROM json_store WHERE namespace = ? AND key = ?",
                (namespace, key),
            )
            conn.commit()

    def has_records(self, namespace: str) -> bool:
        self.ensure_db()
        with self.lock, self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM json_store WHERE namespace = ? LIMIT 1",
                (namespace,),
            ).fetchone()
        return row is not None
