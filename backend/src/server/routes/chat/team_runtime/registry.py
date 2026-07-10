"""进程内 active TeamRun 注册表。"""

import asyncio
from typing import Any


_active_services: dict[str, Any] = {}
_lock = asyncio.Lock()


async def register_team_service(chat_run_id: str, service: Any) -> None:
    async with _lock:
        _active_services[chat_run_id] = service


async def unregister_team_service(chat_run_id: str, service: Any) -> None:
    async with _lock:
        if _active_services.get(chat_run_id) is service:
            _active_services.pop(chat_run_id, None)


async def get_team_service(chat_run_id: str) -> Any | None:
    async with _lock:
        return _active_services.get(chat_run_id)
