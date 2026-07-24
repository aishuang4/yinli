"""Projection Stream 的 fencing、原子 frame 提交与周期 Snapshot checkpoint。"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .block_lifecycle import BlockLifecycleCoordinator
from .domain_events import DomainEvent, DomainEventRepository
from .frame_schema import DisplaySnapshot, RuntimeFrame, decode_runtime_frame, dump_protocol, empty_snapshot


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


class ProjectionConflict(RuntimeError):
    """checkpoint、seq 或 fence 已变化，当前提交必须放弃。"""


class ProjectionLeaseUnavailable(RuntimeError):
    """另一个有效 runner 正持有该 stream。"""


@dataclass(frozen=True, slots=True)
class ProjectionLease:
    stream_key: str
    owner: str
    fence: int
    expires_at: str


@dataclass(frozen=True, slots=True)
class ProjectionState:
    stream_key: str
    run_id: str
    last_domain_ordinal: int
    last_seq: int
    snapshot_seq: int
    snapshot: DisplaySnapshot
    snapshot_updated_at: str


class ProjectionStreamStore:
    """所有 Projection Frame 只能通过此 store 原子提交。"""

    def __init__(
        self,
        *,
        ensure_db=None,
        connect=None,
        lock=None,
        checkpoint_frame_interval: int = 128,
        checkpoint_seconds: float = 2.0,
    ) -> None:
        if ensure_db is None or connect is None or lock is None:
            from src.storage import database

            ensure_db = ensure_db or database.ensure_db
            connect = connect or database._connect
            lock = lock or database._lock
        self.ensure_db = ensure_db
        self.connect = connect
        self.lock = lock
        self.checkpoint_frame_interval = checkpoint_frame_interval
        self.checkpoint_seconds = checkpoint_seconds

    def ensure_stream(self, stream_key: str, run_id: str, *, scope: str = "chat") -> None:
        """幂等创建 Projection Stream，并保证领域流已存在。"""

        DomainEventRepository(
            ensure_db=self.ensure_db,
            connect=self.connect,
            lock=self.lock,
        ).ensure_stream(stream_key, run_id)
        now = _iso(_utc_now())
        snapshot_json = json.dumps(dump_protocol(empty_snapshot()), separators=(",", ":"))
        with self.lock, self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT OR IGNORE INTO projection_streams (
                    stream_key, run_id, scope, snapshot_json,
                    snapshot_updated_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (stream_key, run_id, scope, snapshot_json, now, now),
            )
            row = conn.execute(
                "SELECT run_id FROM projection_streams WHERE stream_key = ?",
                (stream_key,),
            ).fetchone()
            if row is None or str(row["run_id"]) != run_id:
                raise ValueError(f"Projection stream 已绑定其他 Run: {stream_key}")
            conn.commit()

    def acquire_lease(
        self,
        stream_key: str,
        owner: str,
        *,
        lease_seconds: float = 30.0,
        now: datetime | None = None,
    ) -> ProjectionLease:
        """取得独占 lease；新 owner 接管时递增 fencing token。"""

        self.ensure_db()
        current = now or _utc_now()
        expires = current + timedelta(seconds=lease_seconds)
        with self.lock, self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT runner_fence, lease_owner, lease_expires_at
                FROM projection_streams WHERE stream_key = ?
                """,
                (stream_key,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Projection stream 不存在: {stream_key}")
            active_owner = str(row["lease_owner"] or "")
            active_until = self._parse_time(row["lease_expires_at"])
            if active_owner and active_owner != owner and active_until and active_until > current:
                raise ProjectionLeaseUnavailable(f"stream 正由 {active_owner} 投影")
            if active_owner == owner and active_until and active_until > current:
                fence = int(row["runner_fence"])
            else:
                fence = int(row["runner_fence"]) + 1
            conn.execute(
                """
                UPDATE projection_streams
                SET runner_fence = ?, lease_owner = ?, lease_expires_at = ?, updated_at = ?
                WHERE stream_key = ?
                """,
                (fence, owner, _iso(expires), _iso(current), stream_key),
            )
            conn.commit()
        return ProjectionLease(stream_key, owner, fence, _iso(expires))

    def release_lease(self, lease: ProjectionLease) -> None:
        """仅当前 fence 的 owner 可以释放 lease。"""

        with self.lock, self.connect() as conn:
            conn.execute(
                """
                UPDATE projection_streams
                SET lease_owner = NULL, lease_expires_at = NULL, updated_at = ?
                WHERE stream_key = ? AND runner_fence = ? AND lease_owner = ?
                """,
                (_iso(_utc_now()), lease.stream_key, lease.fence, lease.owner),
            )
            conn.commit()

    def load_materialized(
        self,
        stream_key: str,
        coordinator: BlockLifecycleCoordinator,
    ) -> ProjectionState:
        """从周期 Snapshot + tail Frame 恢复到最新 last_seq。"""

        self.ensure_db()
        with self.lock, self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM projection_streams WHERE stream_key = ?",
                (stream_key,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Projection stream 不存在: {stream_key}")
            frame_rows = conn.execute(
                """
                SELECT frame_json FROM projection_frames
                WHERE stream_key = ? AND seq > ? ORDER BY seq ASC
                """,
                (stream_key, int(row["snapshot_seq"])),
            ).fetchall()
        snapshot = DisplaySnapshot.model_validate(json.loads(str(row["snapshot_json"])))
        if snapshot.last_seq != int(row["snapshot_seq"]):
            raise ProjectionConflict("持久化 Snapshot lastSeq 与 snapshot_seq 不一致")
        frames = [decode_runtime_frame(json.loads(str(item["frame_json"]))) for item in frame_rows]
        materialized = coordinator.apply_frames(snapshot, frames)
        if materialized.last_seq != int(row["last_seq"]):
            raise ProjectionConflict("Snapshot + tail 无法恢复到 last_seq")
        return ProjectionState(
            stream_key=stream_key,
            run_id=str(row["run_id"]),
            last_domain_ordinal=int(row["last_domain_ordinal"]),
            last_seq=int(row["last_seq"]),
            snapshot_seq=int(row["snapshot_seq"]),
            snapshot=materialized,
            snapshot_updated_at=str(row["snapshot_updated_at"]),
        )

    def commit_event(
        self,
        lease: ProjectionLease,
        event: DomainEvent,
        frames: list[RuntimeFrame],
        snapshot: DisplaySnapshot,
        *,
        now: datetime | None = None,
    ) -> ProjectionState:
        """原子写入 seq/replay/checkpoint；零 Frame 事件也推进领域水位。"""

        current = now or _utc_now()
        now_text = _iso(current)
        with self.lock, self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM projection_streams WHERE stream_key = ?",
                (lease.stream_key,),
            ).fetchone()
            if row is None:
                raise ProjectionConflict("Projection stream 已不存在")
            self._validate_commit(row, lease, event, frames, snapshot, current)
            for frame in frames:
                conn.execute(
                    """
                    INSERT INTO projection_frames(stream_key, seq, frame_json, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        lease.stream_key,
                        frame.seq,
                        json.dumps(dump_protocol(frame), ensure_ascii=False, separators=(",", ":")),
                        now_text,
                    ),
                )
            checkpoint = self._should_checkpoint(row, snapshot, current)
            if checkpoint:
                conn.execute(
                    """
                    UPDATE projection_streams SET
                        last_domain_ordinal = ?, last_seq = ?, stream_done = ?,
                        snapshot_seq = ?, snapshot_json = ?, snapshot_version = 4,
                        snapshot_updated_at = ?, updated_at = ?
                    WHERE stream_key = ?
                    """,
                    (
                        event.stream_ordinal,
                        snapshot.last_seq,
                        int(snapshot.stream_done),
                        snapshot.last_seq,
                        json.dumps(dump_protocol(snapshot), ensure_ascii=False, separators=(",", ":")),
                        now_text,
                        now_text,
                        lease.stream_key,
                    ),
                )
                snapshot_seq = snapshot.last_seq
                snapshot_updated_at = now_text
            else:
                conn.execute(
                    """
                    UPDATE projection_streams SET
                        last_domain_ordinal = ?, last_seq = ?, stream_done = ?, updated_at = ?
                    WHERE stream_key = ?
                    """,
                    (
                        event.stream_ordinal,
                        snapshot.last_seq,
                        int(snapshot.stream_done),
                        now_text,
                        lease.stream_key,
                    ),
                )
                snapshot_seq = int(row["snapshot_seq"])
                snapshot_updated_at = str(row["snapshot_updated_at"])
            conn.commit()
        return ProjectionState(
            stream_key=lease.stream_key,
            run_id=event.run_id,
            last_domain_ordinal=event.stream_ordinal,
            last_seq=snapshot.last_seq,
            snapshot_seq=snapshot_seq,
            snapshot=snapshot,
            snapshot_updated_at=snapshot_updated_at,
        )

    def frames_after(self, stream_key: str, after_seq: int) -> list[RuntimeFrame]:
        """读取已提交 replay log；SSE 不依赖内存 callback。"""

        self.ensure_db()
        with self.lock, self.connect() as conn:
            rows = conn.execute(
                """
                SELECT frame_json FROM projection_frames
                WHERE stream_key = ? AND seq > ? ORDER BY seq ASC
                """,
                (stream_key, after_seq),
            ).fetchall()
        return [decode_runtime_frame(json.loads(str(row["frame_json"]))) for row in rows]

    def replay_bounds(self, stream_key: str) -> tuple[int | None, int | None]:
        """返回当前 retention 窗口内最小和最大 Frame seq。"""

        self.ensure_db()
        with self.lock, self.connect() as conn:
            row = conn.execute(
                """
                SELECT MIN(seq) AS min_seq, MAX(seq) AS max_seq
                FROM projection_frames WHERE stream_key = ?
                """,
                (stream_key,),
            ).fetchone()
        if row is None or row["min_seq"] is None:
            return None, None
        return int(row["min_seq"]), int(row["max_seq"])

    def compact(self, stream_key: str) -> int:
        """只删除已被 Snapshot 覆盖的 replay Frame。"""

        with self.lock, self.connect() as conn:
            row = conn.execute(
                "SELECT snapshot_seq FROM projection_streams WHERE stream_key = ?",
                (stream_key,),
            ).fetchone()
            if row is None:
                return 0
            cursor = conn.execute(
                "DELETE FROM projection_frames WHERE stream_key = ? AND seq <= ?",
                (stream_key, int(row["snapshot_seq"])),
            )
            conn.commit()
            return int(cursor.rowcount)

    def _validate_commit(
        self,
        row: sqlite3.Row,
        lease: ProjectionLease,
        event: DomainEvent,
        frames: list[RuntimeFrame],
        snapshot: DisplaySnapshot,
        current: datetime,
    ) -> None:
        expires = self._parse_time(row["lease_expires_at"])
        if (
            int(row["runner_fence"]) != lease.fence
            or str(row["lease_owner"] or "") != lease.owner
            or expires is None
            or expires <= current
        ):
            raise ProjectionConflict("runner fence/lease 已失效")
        if event.stream_key != lease.stream_key:
            raise ProjectionConflict("Domain Event 不属于当前 stream")
        if event.run_id != str(row["run_id"]):
            raise ProjectionConflict("Domain Event 不属于当前 Run")
        if event.stream_ordinal != int(row["last_domain_ordinal"]) + 1:
            raise ProjectionConflict("Domain Event ordinal 与 checkpoint 不连续")
        expected_seq = int(row["last_seq"]) + 1
        for frame in frames:
            if hasattr(frame, "snapshot"):
                raise ProjectionConflict("SnapshotFrame 不得写入 replay log")
            if frame.seq != expected_seq:
                raise ProjectionConflict("Projection Frame seq 不连续")
            expected_seq += 1
        if snapshot.last_seq != expected_seq - 1:
            raise ProjectionConflict("Snapshot lastSeq 与提交 Frame 不一致")

    def _should_checkpoint(
        self,
        row: sqlite3.Row,
        snapshot: DisplaySnapshot,
        current: datetime,
    ) -> bool:
        if snapshot.stream_done:
            return True
        if snapshot.last_seq - int(row["snapshot_seq"]) >= self.checkpoint_frame_interval:
            return True
        last_checkpoint = self._parse_time(row["snapshot_updated_at"])
        return bool(
            snapshot.last_seq > int(row["snapshot_seq"])
            and last_checkpoint
            and (current - last_checkpoint).total_seconds() >= self.checkpoint_seconds
        )

    @staticmethod
    def _parse_time(value: Any) -> datetime | None:
        if not value:
            return None
        parsed = datetime.fromisoformat(str(value))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
