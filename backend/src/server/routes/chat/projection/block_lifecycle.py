"""Block Tree 公共生命周期校验与确定性 patch 合并。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from .frame_schema import (
    BlockCloseFrame,
    BlockDeltaFrame,
    BlockOpenFrame,
    DisplayBlock,
    DisplaySnapshot,
    DoneFrame,
    RuntimeFrame,
    SnapshotFrame,
)
from .projection_registry import ProjectionMutation


class BlockLifecycleError(ValueError):
    """Frame 违反 Block Tree 公共不变量。"""


@dataclass(frozen=True, slots=True)
class BlockDataModule:
    """后端投影侧的 kind 初始值与纯 reducer。"""

    empty: Any
    reduce: Any


def _merge_mapping(data: Any, patch: Any) -> dict[str, Any]:
    if data is None:
        data = {}
    if not isinstance(data, dict) or not isinstance(patch, dict):
        raise BlockLifecycleError("结构化 Block patch 必须为对象")
    return {**data, **patch}


def _append_text(data: Any, patch: Any) -> str:
    if data is None:
        data = ""
    if not isinstance(data, str):
        raise BlockLifecycleError("文本 Block data 必须为字符串")
    if isinstance(patch, dict) and isinstance(patch.get("replace"), str):
        return str(patch["replace"])
    if not isinstance(patch, str):
        raise BlockLifecycleError("文本 Block patch 必须为字符串或 replace 对象")
    return data + patch


def _reduce_tool(data: Any, patch: Any) -> dict[str, Any]:
    if data is None:
        data = {}
    if not isinstance(data, dict) or not isinstance(patch, dict):
        raise BlockLifecycleError("tool Block patch 必须为对象")
    result = dict(data)
    for key, value in patch.items():
        if key == "inputDelta":
            if not isinstance(value, str):
                raise BlockLifecycleError("tool.inputDelta 必须为字符串")
            result["inputPreview"] = str(result.get("inputPreview") or "") + value
        elif key == "outputDelta":
            if not isinstance(value, str):
                raise BlockLifecycleError("tool.outputDelta 必须为字符串")
            result["outputPreview"] = str(result.get("outputPreview") or "") + value
        else:
            result[key] = value
    return result


DEFAULT_BLOCK_MODULES = {
    "message": BlockDataModule(empty={}, reduce=_merge_mapping),
    "text": BlockDataModule(empty="", reduce=_append_text),
    "reasoning": BlockDataModule(empty="", reduce=_append_text),
    "thinking": BlockDataModule(empty="", reduce=_append_text),
    "tool": BlockDataModule(empty={}, reduce=_reduce_tool),
    "skill": BlockDataModule(empty={}, reduce=_merge_mapping),
    "approval": BlockDataModule(empty={}, reduce=_merge_mapping),
    "plan": BlockDataModule(empty={}, reduce=_merge_mapping),
    "artifact": BlockDataModule(empty={}, reduce=_merge_mapping),
    "error": BlockDataModule(empty={}, reduce=_merge_mapping),
    "subagent": BlockDataModule(empty={}, reduce=_merge_mapping),
    "team": BlockDataModule(empty={}, reduce=_merge_mapping),
}


class BlockLifecycleCoordinator:
    """唯一负责 parent、open/delta/close、容量与终态 barrier 的组件。"""

    def __init__(
        self,
        *,
        modules: dict[str, BlockDataModule] | None = None,
        max_depth: int = 16,
        max_blocks: int = 2_000,
    ) -> None:
        self.modules = modules or DEFAULT_BLOCK_MODULES
        self.max_depth = max_depth
        self.max_blocks = max_blocks

    def apply_mutations(
        self,
        snapshot: DisplaySnapshot,
        mutations: list[ProjectionMutation],
    ) -> tuple[DisplaySnapshot, list[RuntimeFrame]]:
        """在副本上原子应用候选变更，并为最终 Frame 连续分配 seq。"""

        work = snapshot.model_copy(deep=True)
        frames: list[RuntimeFrame] = []
        for mutation in mutations:
            if mutation.op == "close" and mutation.outcome in {"failed", "cancelled"}:
                for descendant_id in self._open_descendants_leaf_first(work, mutation.id):
                    frame = BlockCloseFrame(
                        seq=work.last_seq + 1,
                        id=descendant_id,
                        outcome="cancelled",
                    )
                    self._apply_frame(work, frame)
                    frames.append(frame)
            frame = self._frame_from_mutation(work.last_seq + 1, mutation)
            self._apply_frame(work, frame)
            frames.append(frame)
        return work, frames

    def apply_frames(
        self,
        snapshot: DisplaySnapshot,
        frames: list[RuntimeFrame],
    ) -> DisplaySnapshot:
        """用同一 reducer 重放持久化 tail Frame。"""

        work = snapshot.model_copy(deep=True)
        for frame in frames:
            if isinstance(frame, SnapshotFrame):
                work = frame.snapshot.model_copy(deep=True)
                continue
            if frame.seq <= work.last_seq:
                continue
            if frame.seq != work.last_seq + 1:
                raise BlockLifecycleError(
                    f"Frame seq 不连续: expected={work.last_seq + 1}, actual={frame.seq}"
                )
            self._apply_frame(work, frame)
        return work

    def _apply_frame(self, snapshot: DisplaySnapshot, frame: RuntimeFrame) -> None:
        if isinstance(frame, BlockOpenFrame):
            self._open(snapshot, frame)
        elif isinstance(frame, BlockDeltaFrame):
            self._delta(snapshot, frame)
        elif isinstance(frame, BlockCloseFrame):
            self._close(snapshot, frame)
        elif isinstance(frame, DoneFrame):
            if snapshot.stream_done:
                raise BlockLifecycleError("stream 已 done")
            if any(block.phase == "open" for block in snapshot.blocks_by_id.values()):
                raise BlockLifecycleError("仍有 open Block，不能提交 done")
            snapshot.stream_done = True
        else:
            raise BlockLifecycleError("SnapshotFrame 只能用于原子 hydrate")
        snapshot.last_seq = frame.seq

    def _open(self, snapshot: DisplaySnapshot, frame: BlockOpenFrame) -> None:
        if snapshot.stream_done:
            raise BlockLifecycleError("done 后不能 open Block")
        if frame.id in snapshot.blocks_by_id:
            raise BlockLifecycleError(f"Block 重复 open: {frame.id}")
        if len(snapshot.order) >= self.max_blocks:
            raise BlockLifecycleError("Block 数量超过上限")
        if frame.parent:
            parent = snapshot.blocks_by_id.get(frame.parent)
            if parent is None:
                raise BlockLifecycleError(f"parent 不存在: {frame.parent}")
            if parent.phase != "open":
                raise BlockLifecycleError(f"parent 已关闭: {frame.parent}")
            if self._depth(snapshot, frame.parent) + 1 > self.max_depth:
                raise BlockLifecycleError("Block 嵌套深度超过上限")
        module = self.modules.get(frame.kind)
        data = deepcopy(module.empty) if module is not None else {}
        if frame.patch is not None and module is not None:
            data = module.reduce(data, frame.patch)
        snapshot.blocks_by_id[frame.id] = DisplayBlock(
            id=frame.id,
            parent=frame.parent,
            kind=frame.kind,
            data_version=frame.data_version,
            data=data,
        )
        snapshot.order.append(frame.id)

    def _delta(self, snapshot: DisplaySnapshot, frame: BlockDeltaFrame) -> None:
        block = self._open_block(snapshot, frame.id)
        module = self.modules.get(block.kind)
        if module is None:
            raise BlockLifecycleError(f"未知 kind 不接受 delta: {block.kind}")
        block.data = module.reduce(deepcopy(block.data), frame.patch)

    def _close(self, snapshot: DisplaySnapshot, frame: BlockCloseFrame) -> None:
        block = self._open_block(snapshot, frame.id)
        if frame.outcome == "completed" and self._open_descendants_leaf_first(snapshot, frame.id):
            raise BlockLifecycleError(f"Block 仍有 open descendant: {frame.id}")
        if frame.patch is not None:
            module = self.modules.get(block.kind)
            if module is None:
                raise BlockLifecycleError(f"未知 kind 不接受 close patch: {block.kind}")
            block.data = module.reduce(deepcopy(block.data), frame.patch)
        block.phase = "closed"
        block.outcome = frame.outcome

    @staticmethod
    def _open_block(snapshot: DisplaySnapshot, block_id: str) -> DisplayBlock:
        block = snapshot.blocks_by_id.get(block_id)
        if block is None:
            raise BlockLifecycleError(f"Block 不存在: {block_id}")
        if block.phase != "open":
            raise BlockLifecycleError(f"Block 已关闭: {block_id}")
        return block

    @staticmethod
    def _depth(snapshot: DisplaySnapshot, block_id: str) -> int:
        depth = 1
        block = snapshot.blocks_by_id[block_id]
        seen = {block_id}
        while block.parent:
            if block.parent in seen:
                raise BlockLifecycleError("Block parent 出现环")
            seen.add(block.parent)
            block = snapshot.blocks_by_id[block.parent]
            depth += 1
        return depth

    def _open_descendants_leaf_first(
        self,
        snapshot: DisplaySnapshot,
        block_id: str,
    ) -> list[str]:
        if block_id not in snapshot.blocks_by_id:
            return []
        descendants: list[tuple[int, int, str]] = []
        for index, candidate_id in enumerate(snapshot.order):
            candidate = snapshot.blocks_by_id[candidate_id]
            if candidate.phase != "open" or candidate_id == block_id:
                continue
            parent = candidate.parent
            while parent:
                if parent == block_id:
                    descendants.append((self._depth(snapshot, candidate_id), index, candidate_id))
                    break
                parent_block = snapshot.blocks_by_id.get(parent)
                parent = parent_block.parent if parent_block else None
        descendants.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [item[2] for item in descendants]

    @staticmethod
    def _frame_from_mutation(seq: int, mutation: ProjectionMutation) -> RuntimeFrame:
        if mutation.op == "open":
            if not mutation.id or not mutation.kind:
                raise BlockLifecycleError("open mutation 缺少 id/kind")
            return BlockOpenFrame(
                seq=seq,
                id=mutation.id,
                parent=mutation.parent,
                kind=mutation.kind,
                data_version=mutation.data_version,
                patch=mutation.patch,
            )
        if mutation.op == "delta":
            return BlockDeltaFrame(seq=seq, id=mutation.id, patch=mutation.patch)
        if mutation.op == "close":
            if mutation.outcome is None:
                raise BlockLifecycleError("close mutation 缺少 outcome")
            return BlockCloseFrame(
                seq=seq,
                id=mutation.id,
                outcome=mutation.outcome,
                patch=mutation.patch,
            )
        return DoneFrame(seq=seq)
