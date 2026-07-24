"""基于已提交 Projection log 提供 afterSeq replay 与 Snapshot fallback。"""

from __future__ import annotations

from dataclasses import dataclass

from .block_lifecycle import BlockLifecycleCoordinator
from .frame_schema import DisplaySnapshot, RuntimeFrame, SnapshotFrame
from .projection_writer import ProjectionStreamStore


@dataclass(frozen=True, slots=True)
class ProjectionStreamBatch:
    """一次离线读取结果，生产 SSE 只需逐帧发送 frames。"""

    frames: list[RuntimeFrame]
    snapshot_fallback: bool
    stream_done: bool
    last_seq: int


class ProjectionStreamService:
    """读取持久化流，不依赖 runtime callback 或旧 RunState。"""

    def __init__(
        self,
        *,
        store: ProjectionStreamStore,
        coordinator: BlockLifecycleCoordinator,
    ) -> None:
        self.store = store
        self.coordinator = coordinator

    def read(self, stream_key: str, *, after_seq: int | None = None) -> ProjectionStreamBatch:
        """连续 replay 可证明时返回 tail，否则用最新完整 Snapshot 原子替换。"""

        if after_seq is not None and after_seq < 0:
            raise ValueError("afterSeq 不能为负数")
        state = self.store.load_materialized(stream_key, self.coordinator)
        if after_seq is None or after_seq > state.last_seq:
            return self._snapshot_batch(state.snapshot)
        if after_seq == state.last_seq:
            return ProjectionStreamBatch(
                frames=[],
                snapshot_fallback=False,
                stream_done=state.snapshot.stream_done,
                last_seq=state.last_seq,
            )
        frames = self.store.frames_after(stream_key, after_seq)
        min_seq, _ = self.store.replay_bounds(stream_key)
        if min_seq is None or min_seq > after_seq + 1 or not frames:
            return self._snapshot_batch(state.snapshot)
        expected = after_seq + 1
        for frame in frames:
            if frame.seq != expected:
                return self._snapshot_batch(state.snapshot)
            expected += 1
        if frames[-1].seq != state.last_seq:
            return self._snapshot_batch(state.snapshot)
        return ProjectionStreamBatch(
            frames=frames,
            snapshot_fallback=False,
            stream_done=state.snapshot.stream_done,
            last_seq=state.last_seq,
        )

    @staticmethod
    def heartbeat_comment() -> str:
        """heartbeat 使用 SSE comment，不生成 RuntimeFrame 或 seq。"""

        return ": heartbeat\n\n"

    @staticmethod
    def _snapshot_batch(snapshot: DisplaySnapshot) -> ProjectionStreamBatch:
        frame = SnapshotFrame(seq=snapshot.last_seq, snapshot=snapshot)
        return ProjectionStreamBatch(
            frames=[frame],
            snapshot_fallback=True,
            stream_done=snapshot.stream_done,
            last_seq=snapshot.last_seq,
        )
