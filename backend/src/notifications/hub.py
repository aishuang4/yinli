"""应用内通知订阅中心。独立于 Chat SSE，只服务 `/api/notifications/stream`。"""

from __future__ import annotations

import asyncio
from typing import Any


class NotificationHub:
    """进程内 fan-out。订阅可按 project_id 过滤，空过滤器接收全部。"""

    def __init__(self) -> None:
        self._subscribers: list[tuple[str, str | None, asyncio.Queue[dict[str, Any]]]] = []

    def subscribe(self, project_id: str | None = None) -> asyncio.Queue[dict[str, Any]]:
        """注册一个订阅队列。只接收当前租户的事件。"""

        from src.tenancy.context import current_tenant

        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=64)
        self._subscribers.append((current_tenant().tenant_id, project_id or None, queue))
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        """移除订阅。"""

        self._subscribers = [item for item in self._subscribers if item[1] is not queue]

    def publish(self, event: dict[str, Any]) -> None:
        """向匹配的订阅者投递事件；队列满则丢弃该订阅以免阻塞 firing。"""

        from src.tenancy.context import current_tenant

        tenant_id = current_tenant().tenant_id
        project_id = str(event.get("projectId") or "")
        stale: list[asyncio.Queue[dict[str, Any]]] = []
        for subscriber_tenant, filter_project, queue in list(self._subscribers):
            if subscriber_tenant != tenant_id:
                continue
            if filter_project and filter_project != project_id:
                continue
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                stale.append(queue)
        for queue in stale:
            self.unsubscribe(queue)


notification_hub = NotificationHub()
