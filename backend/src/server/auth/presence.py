"""流式连接存续时刷新会话最近活跃时间。"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import TypeVar

from src.storage import auth_records

T = TypeVar("T")
_TOUCH_INTERVAL_SECONDS = 25.0


def _touch(raw_token: str) -> None:
    if not raw_token:
        return
    session = auth_records.get_session_by_token(raw_token)
    if session is not None:
        auth_records.touch_session(session["id"])


async def iter_with_presence(source: AsyncIterator[T], raw_token: str) -> AsyncIterator[T]:
    """每产生一块流式输出就尝试刷新会话，避免长连接被看成离线。"""

    last_touch = 0.0
    async for chunk in source:
        now = time.monotonic()
        if raw_token and now - last_touch >= _TOUCH_INTERVAL_SECONDS:
            _touch(raw_token)
            last_touch = now
        yield chunk
