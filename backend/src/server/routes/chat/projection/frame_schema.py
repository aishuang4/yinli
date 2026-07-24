"""Event Stream V3 的固定 RuntimeFrame 与 DisplaySnapshot V4 schema。"""

from __future__ import annotations

from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator


BlockOutcome: TypeAlias = Literal["completed", "failed", "cancelled"]


class ProtocolModel(BaseModel):
    """统一使用 camelCase 输出且拒绝未声明的协议字段。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid", strict=True)


class DisplayBlock(ProtocolModel):
    """规范化 Block Tree 中的单个展示节点。"""

    id: str
    parent: str | None = None
    kind: str
    data_version: int = Field(alias="dataVersion", ge=1)
    phase: Literal["open", "closed"] = "open"
    outcome: BlockOutcome | None = None
    data: Any

    @model_validator(mode="after")
    def validate_phase(self) -> "DisplayBlock":
        if self.phase == "open" and self.outcome is not None:
            raise ValueError("open Block 不能包含 outcome")
        if self.phase == "closed" and self.outcome is None:
            raise ValueError("closed Block 必须包含 outcome")
        return self


class DisplaySnapshot(ProtocolModel):
    """刷新、恢复和历史回放共用的完整 Block Tree。"""

    version: Literal[4] = 4
    last_seq: int = Field(default=0, alias="lastSeq", ge=0)
    stream_done: bool = Field(default=False, alias="streamDone")
    order: list[str] = Field(default_factory=list)
    blocks_by_id: dict[str, DisplayBlock] = Field(default_factory=dict, alias="blocksById")

    @model_validator(mode="after")
    def validate_tree(self) -> "DisplaySnapshot":
        if len(set(self.order)) != len(self.order):
            raise ValueError("Snapshot order 包含重复 Block ID")
        if set(self.order) != set(self.blocks_by_id):
            raise ValueError("Snapshot order 与 blocksById 不一致")
        if len(self.order) > self.last_seq:
            raise ValueError("Snapshot Block 数不能超过 lastSeq")
        positions = {block_id: index for index, block_id in enumerate(self.order)}
        for block_id in self.order:
            block = self.blocks_by_id[block_id]
            parent = block.parent
            depth = 1
            seen = {block_id}
            while parent:
                if parent not in self.blocks_by_id or positions[parent] >= positions[block_id]:
                    raise ValueError(f"Snapshot parent 顺序非法: {block_id}")
                if parent in seen:
                    raise ValueError(f"Snapshot parent 出现环: {block_id}")
                seen.add(parent)
                depth += 1
                if depth > 16:
                    raise ValueError(f"Snapshot Block 深度超过上限: {block_id}")
                parent = self.blocks_by_id[parent].parent
        if self.stream_done and any(block.phase == "open" for block in self.blocks_by_id.values()):
            raise ValueError("streamDone Snapshot 不能包含 open Block")
        return self


class BlockOpenFrame(ProtocolModel):
    seq: int = Field(ge=1)
    op: Literal["open"] = "open"
    id: str
    parent: str | None = None
    kind: str
    data_version: int = Field(alias="dataVersion", ge=1)
    patch: Any = None


class BlockDeltaFrame(ProtocolModel):
    seq: int = Field(ge=1)
    op: Literal["delta"] = "delta"
    id: str
    patch: Any


class BlockCloseFrame(ProtocolModel):
    seq: int = Field(ge=1)
    op: Literal["close"] = "close"
    id: str
    outcome: BlockOutcome
    patch: Any = None


class SnapshotFrame(ProtocolModel):
    seq: int = Field(ge=0)
    snapshot: DisplaySnapshot

    @model_validator(mode="after")
    def validate_watermark(self) -> "SnapshotFrame":
        if self.seq != self.snapshot.last_seq:
            raise ValueError("Snapshot frame seq 必须等于 snapshot.lastSeq")
        return self


class DoneFrame(ProtocolModel):
    seq: int = Field(ge=1)
    done: Literal[True] = True


RuntimeFrame: TypeAlias = (
    BlockOpenFrame | BlockDeltaFrame | BlockCloseFrame | SnapshotFrame | DoneFrame
)
RUNTIME_FRAME_ADAPTER = TypeAdapter(RuntimeFrame)


def empty_snapshot() -> DisplaySnapshot:
    """创建一个未消费任何 Frame 的 V4 Snapshot。"""

    return DisplaySnapshot()


def decode_runtime_frame(value: Any) -> RuntimeFrame:
    """在持久化边界和 SSE 边界严格解码 RuntimeFrame。"""

    return RUNTIME_FRAME_ADAPTER.validate_python(value)


def dump_protocol(model: ProtocolModel) -> dict[str, Any]:
    """输出可直接 JSON 序列化的协议对象。"""

    return model.model_dump(by_alias=True, mode="json", exclude_none=True)
