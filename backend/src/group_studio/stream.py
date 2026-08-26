"""群聊工作室进程内 SSE 订阅枢纽。不经过 Chat projector。"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from typing import Any, AsyncGenerator

_SUBSCRIBERS: dict[str, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)


async def subscribe(group_id: str) -> AsyncGenerator[dict[str, Any], None]:
    """订阅一个工作室的实时事件；超时发送由路由层写 heartbeat。"""

    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    _SUBSCRIBERS[group_id].add(queue)
    try:
        while True:
            event = await queue.get()
            yield event
    finally:
        _SUBSCRIBERS[group_id].discard(queue)
        if not _SUBSCRIBERS[group_id]:
            _SUBSCRIBERS.pop(group_id, None)


async def publish(group_id: str, event: dict[str, Any]) -> None:
    """把信封推给当前进程订阅者。"""

    for queue in list(_SUBSCRIBERS.get(group_id, set())):
        await queue.put(event)


def format_sse(event: dict[str, Any]) -> str:
    """按契约输出 named event + data JSON。"""

    event_type = str(event.get("type") or "GROUP_ERROR")
    return f"event: {event_type}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


def heartbeat_comment() -> str:
    return ": heartbeat\n\n"
