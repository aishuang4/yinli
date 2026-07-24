"""Assistant text Block 的离线 projector。"""

from __future__ import annotations

from typing import Iterable

from ..domain_events import DomainEvent
from ..frame_schema import DisplaySnapshot
from ..projection_registry import ProjectionMutation

EVENT_TYPES = {"agent.output.started", "agent.output.delta", "agent.output.completed"}


def project(event: DomainEvent, snapshot: DisplaySnapshot) -> Iterable[ProjectionMutation]:
    data = event.data
    block_id = str(data.get("blockId") or event.aggregate_id)
    if event.type == "agent.output.started":
        parent_id = str(data.get("parentId") or "")
        if not parent_id:
            raise ValueError("agent.output.started 缺少 parentId")
        initial = data.get("initial", data.get("content", ""))
        if not isinstance(initial, str):
            raise ValueError("text 初始内容必须为字符串")
        yield ProjectionMutation(
            op="open",
            id=block_id,
            parent=parent_id,
            kind="text",
            patch=initial,
        )
    elif event.type == "agent.output.delta":
        patch = data.get("patch", data.get("delta"))
        if not isinstance(patch, (str, dict)):
            raise ValueError("agent.output.delta 缺少合法 patch")
        yield ProjectionMutation(op="delta", id=block_id, patch=patch)
    else:
        yield ProjectionMutation(op="close", id=block_id, outcome="completed")
