"""旧的整段记忆摘要缓存。

运行时不再注入这段文本。记忆页改写旧文件时仍会清掉它。
"""

from __future__ import annotations

from collections.abc import Callable

from src.agents.session_context import session_context_key
from src.memory.prompt_cache import _cached

legacy_prompt_cache: dict[str, tuple[str, float]] = {}


def session_memory_prompt(
    *,
    agent_id: str,
    session_id: str | None,
    project_slug: str = "",
    build: Callable[[], str],
) -> str:
    """返回该会话冻结的旧记忆注入文本。没有会话时每次现算。"""

    if not session_id or not agent_id:
        return build()
    key = session_context_key(agent_id, session_id)
    if project_slug:
        key = f"{key}:project:{project_slug}"
    return _cached(legacy_prompt_cache, key, build)
