"""Tool 摘要 Block 的离线 projector，不保存完整 Observation。"""

from __future__ import annotations

from typing import Any, Iterable

from ..domain_events import DomainEvent
from ..frame_schema import DisplaySnapshot
from ..projection_registry import ProjectionMutation

EVENT_TYPES = {
    "tool.call.started",
    "tool.call.delta",
    "tool.call.arguments",
    "tool.result.delta",
    "tool.result.completed",
    "tool.result.failed",
    "tool.result.cancelled",
}
_STRING_FIELDS = {
    "name",
    "inputPreview",
    "outputPreview",
    "error",
    "detailRef",
    "traceRef",
    "scope",
    "memberId",
    "taskId",
}


def _safe_patch(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        key: item
        for key, item in value.items()
        if key in _STRING_FIELDS and isinstance(item, str)
    }


def project(event: DomainEvent, snapshot: DisplaySnapshot) -> Iterable[ProjectionMutation]:
    data = event.data
    block_id = str(data.get("blockId") or event.aggregate_id)
    if event.type == "tool.call.started":
        parent_id = str(data.get("parentId") or "")
        if not parent_id:
            raise ValueError("tool.call.started 缺少 parentId")
        yield ProjectionMutation(
            op="open",
            id=block_id,
            parent=parent_id,
            kind="tool",
            patch=_safe_patch(data),
        )
        return
    if event.type == "tool.call.delta":
        delta = data.get("delta")
        if not isinstance(delta, str):
            raise ValueError("tool.call.delta 缺少字符串 delta")
        yield ProjectionMutation(op="delta", id=block_id, patch={"inputDelta": delta})
        return
    if event.type == "tool.call.arguments":
        preview = data.get("inputPreview")
        if not isinstance(preview, str):
            raise ValueError("tool.call.arguments 缺少字符串 inputPreview")
        yield ProjectionMutation(op="delta", id=block_id, patch={"inputPreview": preview})
        return
    if event.type == "tool.result.delta":
        delta = data.get("delta")
        if not isinstance(delta, str):
            raise ValueError("tool.result.delta 缺少字符串 delta")
        yield ProjectionMutation(op="delta", id=block_id, patch={"outputDelta": delta})
        return
    failed = event.type.endswith(".failed") or str(data.get("status") or "completed") == "failed"
    outcome = "cancelled" if event.type.endswith(".cancelled") else "failed" if failed else "completed"
    yield ProjectionMutation(
        op="close",
        id=block_id,
        outcome=outcome,
        patch=_safe_patch(data),
    )
