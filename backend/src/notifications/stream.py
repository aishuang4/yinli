"""通知 SSE 生成器。事件名独立于 Chat RUNTIME_EVENT。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Any

from src.notifications.hub import notification_hub
from src.notifications.inbox import unread_count

HEARTBEAT_SECONDS = 15.0


def _sse(event: str, data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


async def notification_event_stream(
    project_id: str | None = None,
) -> AsyncGenerator[str, None]:
    """先推 unread 快照，再转发 hub 事件；超时发送注释心跳。"""

    queue = notification_hub.subscribe(project_id)
    try:
        yield _sse("snapshot", {
            "type": "snapshot",
            "projectSlug": project_id or "",
            "unreadCount": unread_count(project_slug=project_id),
        })
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
            except TimeoutError:
                yield ": ping\n\n"
                continue
            event_type = str(event.get("type") or "notification")
            yield _sse(event_type, event)
    finally:
        notification_hub.unsubscribe(queue)
