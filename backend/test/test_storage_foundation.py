"""阶段 1 存储底座：写事务、备份恢复、健康检查和大对象外置。"""
from __future__ import annotations

from src.storage.records.observation_records import get_trace_observation
from src.storage.records.observation_records import insert_trace_observation


import sqlite3
import threading
import time

import pytest

from src.storage.engine import database
from src.storage.engine.database_schema import FACT_SOURCE_TABLES, LEDGER_TABLES, META_TABLES
from src.storage.engine.maintenance import backup, health, recover
from src.storage.engine.sqlite_tx import StorageLocked, open_autocommit


def test_write_tx_commits_and_rolls_back():
    with database.write_tx() as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        conn.execute(
            """
            INSERT INTO json_store (namespace, key, value, created_at, updated_at)
            VALUES ('foundation', 'kept', '{}', 't', 't')
            """
        )
    assert str(mode).lower() == "wal"
    assert int(timeout) == 1000
    with pytest.raises(RuntimeError, match="boom"):
        with database.write_tx() as conn:
            conn.execute(
                """
                INSERT INTO json_store (namespace, key, value, created_at, updated_at)
                VALUES ('foundation', 'lost', '{}', 't', 't')
                """
            )
            raise RuntimeError("boom")
    with database.read_conn() as conn:
        keys = [row["key"] for row in conn.execute("SELECT key FROM json_store WHERE namespace = 'foundation'")]
    assert keys == ["kept"]


def test_write_tx_retries_until_external_lock_releases():
    database.ensure_db()
    holder = sqlite3.connect(database.db_path(), isolation_level=None, check_same_thread=False)
    holder.execute("BEGIN IMMEDIATE")
    released = threading.Event()

    def release() -> None:
        time.sleep(1.2)
        holder.execute("ROLLBACK")
        holder.close()
        released.set()

    threading.Thread(target=release, daemon=True).start()
    with database.write_tx() as conn:
        conn.execute(
            """
            INSERT INTO json_store (namespace, key, value, created_at, updated_at)
            VALUES ('foundation', 'after-lock', '{}', 't', 't')
            """
        )
    assert released.is_set()


def test_concurrent_writers_and_readers():
    database.ensure_db()
    errors: list[BaseException] = []

    def writer(index: int) -> None:
        try:
            for step in range(15):
                with database.write_tx() as conn:
                    conn.execute(
                        """
                        INSERT INTO json_store (namespace, key, value, created_at, updated_at)
                        VALUES ('load', ?, '{}', 't', 't')
                        """,
                        (f"{index}-{step}",),
                    )
        except BaseException as exc:  # noqa: BLE001 — 压测要收齐全部失败
            errors.append(exc)

    def reader() -> None:
        try:
            for _ in range(20):
                with database.read_conn() as conn:
                    conn.execute("SELECT COUNT(*) AS total FROM json_store").fetchone()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(index,)) for index in range(10)]
    threads.extend(threading.Thread(target=reader) for _ in range(5))
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    with database.read_conn() as conn:
        total = conn.execute(
            "SELECT COUNT(*) AS total FROM json_store WHERE namespace = 'load'"
        ).fetchone()
    assert int(total["total"]) == 150


def test_write_tx_budget_raises_storage_locked():
    database.ensure_db()
    holder = open_autocommit(database.db_path())
    holder.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(StorageLocked):
            with database.write_tx(budget="heartbeat"):
                pass
    finally:
        holder.execute("ROLLBACK")
        holder.close()


def test_backup_recover_keeps_fact_rows_and_skips_ledger():
    with database.write_tx() as conn:
        conn.execute(
            """
            INSERT INTO json_store (namespace, key, value, created_at, updated_at)
            VALUES ('foundation', 'fact', '{"n":1}', 't', 't')
            """
        )
    saved = backup()
    with database.write_tx() as conn:
        conn.execute("DELETE FROM json_store WHERE namespace = 'foundation' AND key = 'fact'")
    restored = recover(saved)
    copied = sqlite3.connect(restored)
    copied.row_factory = sqlite3.Row
    try:
        row = copied.execute(
            "SELECT value FROM json_store WHERE namespace = 'foundation' AND key = 'fact'"
        ).fetchone()
        version = copied.execute("SELECT version FROM schema_version").fetchone()
        tables = {item["name"] for item in copied.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        copied.close()
    assert row["value"] == '{"n":1}'
    assert int(version["version"]) == 1
    assert set(LEDGER_TABLES).isdisjoint(tables)
    live = sqlite3.connect(database.db_path())
    try:
        gone = live.execute(
            "SELECT 1 FROM json_store WHERE namespace = 'foundation' AND key = 'fact'"
        ).fetchone()
    finally:
        live.close()
    assert gone is None


def test_health_reports_wal_and_schema_version():
    database.ensure_db()
    report = health()
    assert report["schemaVersion"] == 1
    assert report["journalMode"] == "wal"
    assert isinstance(report["walBytes"], int)
    assert report["lock"] == "idle"
    assert "lastCheckpointAt" in report


def test_tables_are_classified():
    database.ensure_db()
    with database.read_conn() as conn:
        names = {
            str(row["name"])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    classified = set(FACT_SOURCE_TABLES) | set(META_TABLES)
    assert names <= classified
    assert "schema_version" in names
    assert "trace_observations" in names


def test_trace_payload_over_64kb_spills_to_session_file():
    blob = "x" * 70_000
    insert_trace_observation(
        {
            "id": "obs-big",
            "runId": "run-big",
            "sessionId": "ses-big",
            "kind": "tool",
            "output": {"blob": blob},
        }
    )
    loaded = get_trace_observation("obs-big")
    assert loaded is not None
    assert loaded["output"]["blob"] == blob
    with database.read_conn() as conn:
        row = conn.execute(
            "SELECT payload_path, payload_sha256, output_json FROM trace_observations WHERE id = 'obs-big'"
        ).fetchone()
    assert str(row["payload_path"]).startswith("sessions/ses-big/runs/run-big/")
    assert len(row["payload_sha256"]) == 64
    assert len(row["output_json"]) < 1000

    insert_trace_observation(
        {
            "id": "obs-small",
            "runId": "run-small",
            "sessionId": "ses-small",
            "kind": "tool",
            "output": {"blob": "ok"},
        }
    )
    with database.read_conn() as conn:
        small = conn.execute(
            "SELECT payload_path, output_json FROM trace_observations WHERE id = 'obs-small'"
        ).fetchone()
    assert small["payload_path"] == ""
    assert "ok" in small["output_json"]
