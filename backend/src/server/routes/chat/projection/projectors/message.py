"""Message 根 Block 与 Run 终态的离线 projector。"""

from __future__ import annotations

from typing import Iterable

from ..domain_events import DomainEvent
from ..frame_schema import DisplaySnapshot
from ..projection_registry import ProjectionMutation

EVENT_TYPES = {"message.started", "run.completed", "run.failed", "run.cancelled"}


def project(event: DomainEvent, snapshot: DisplaySnapshot) -> Iterable[ProjectionMutation]:
    data = event.data
    if event.type == "message.started":
        block_id = str(data.get("blockId") or event.aggregate_id)
        initial = {
            key: data[key]
            for key in ("role", "agentName", "createdAt")
            if key in data and isinstance(data[key], str)
        }
        yield ProjectionMutation(op="open", id=block_id, kind="message", patch=initial)
        return

    message_id = str(data.get("messageId") or "")
    if not message_id:
        raise ValueError(f"{event.type} 缺少 messageId")
    outcome = {
        "run.completed": "completed",
        "run.failed": "failed",
        "run.cancelled": "cancelled",
    }[event.type]
    patch = {
        key: data[key]
        for key in ("error", "completedAt")
        if key in data and isinstance(data[key], str)
    }
    yield ProjectionMutation(op="close", id=message_id, outcome=outcome, patch=patch or None)
    yield ProjectionMutation(op="done")
