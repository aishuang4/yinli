"""隔离 kind projector 共用的生命周期翻译 helper。"""

from __future__ import annotations

from collections.abc import Iterable

from ..domain_events import DomainEvent
from ..projection_registry import ProjectionMutation


def project_kind_lifecycle(event: DomainEvent, *, kind: str) -> Iterable[ProjectionMutation]:
    """把单一 kind 的 started/delta/completed/failed/cancelled 事实翻译为 mutation。"""

    data = event.data
    block_id = str(data.get("blockId") or event.aggregate_id)
    if event.type.endswith(".started"):
        parent_id = str(data.get("parentId") or "")
        if not parent_id:
            raise ValueError(f"{event.type} 缺少 parentId")
        yield ProjectionMutation(
            op="open",
            id=block_id,
            parent=parent_id,
            kind=kind,
            data_version=event.data_version,
            patch=data.get("initial"),
        )
        return
    if event.type.endswith(".delta") or event.type.endswith(".updated"):
        if "patch" not in data:
            raise ValueError(f"{event.type} 缺少 patch")
        yield ProjectionMutation(op="delta", id=block_id, patch=data["patch"])
        return
    outcome = "completed"
    if event.type.endswith(".failed"):
        outcome = "failed"
    elif event.type.endswith(".cancelled"):
        outcome = "cancelled"
    yield ProjectionMutation(
        op="close",
        id=block_id,
        outcome=outcome,
        patch=data.get("patch"),
    )
