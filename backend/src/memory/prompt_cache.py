"""按会话冻结的记忆注入文本。

习惯全文按用户冻结，写入后立刻失效，让这一轮回复能读到新习惯。
专业全文按智能体和会话冻结，判断智能体改写后下一轮才换。
旧的整段记忆摘要缓存仍留在这里，避免 Memory 页面改写后继续命中过期文本。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from src.agents.session_context import session_context_key

_lock = threading.Lock()
_cache: dict[str, tuple[str, float]] = {}
_habit_cache: dict[str, tuple[str, float]] = {}
_professional_cache: dict[str, tuple[str, float]] = {}
# 与 factory.AGENT_CACHE_IDLE_SECONDS 保持一致（6h）；不直接 import 以免拖入工厂。
MEMORY_PROMPT_TTL_SECONDS = 6 * 60 * 60


def session_memory_prompt(
    *,
    agent_id: str,
    session_id: str | None,
    project_slug: str = "",
    build: Callable[[], str],
) -> str:
    """返回该会话冻结的旧记忆注入文本；首次（或失效后）调用 `build()` 生成。

    有项目维度时，缓存键带上 project_slug，避免换项目后命中旧文本。
    """

    if not session_id or not agent_id:
        return build()
    key = session_context_key(agent_id, session_id)
    if project_slug:
        key = f"{key}:project:{project_slug}"
    return _cached(_cache, key, build)


def session_habit_prompt(*, user_id: str, build: Callable[[], str]) -> str:
    """返回这个人冻结的习惯全文。没有用户标识时每次现算。"""

    if not user_id:
        return build()
    return _cached(_habit_cache, user_id, build)


def session_professional_prompt(
    *,
    agent_id: str,
    session_id: str | None,
    project_slug: str = "",
    build: Callable[[], str],
) -> str:
    """返回该智能体在这个会话里冻结的专业全文。没有会话时每次现算。"""

    if not agent_id or not session_id:
        return build()
    key = session_context_key(agent_id, session_id)
    if project_slug:
        key = f"{key}:project:{project_slug}"
    return _cached(_professional_cache, key, build)


def invalidate_habit_prompts() -> int:
    """习惯写入后清掉全部习惯缓存，让当前回复重新读取。"""

    with _lock:
        return _drop(_habit_cache, None)


def invalidate_professional_prompts(*, agent_id: str | None = None) -> int:
    """专业区改写后清掉对应智能体的冻结文本。不传 agent_id 时全部清掉。"""

    with _lock:
        return _drop(_professional_cache, agent_id)


def invalidate_memory_prompts(*, agent_id: str | None = None) -> int:
    """让冻结文本失效。不传 agent_id 时习惯、专业和旧摘要一起清掉。"""

    with _lock:
        count = _drop(_cache, agent_id)
        count += _drop(_professional_cache, agent_id)
        if agent_id is None:
            count += _drop(_habit_cache, None)
        return count


def _tenant_prefix() -> str:
    from src.tenancy.context import current_tenant

    return f"{current_tenant().tenant_id}:"


def _cached(store: dict[str, tuple[str, float]], key: str, build: Callable[[], str]) -> str:
    key = _tenant_prefix() + key
    now = time.monotonic()
    with _lock:
        cached = store.get(key)
        if cached is not None and now - cached[1] < MEMORY_PROMPT_TTL_SECONDS:
            store[key] = (cached[0], now)
            return cached[0]
    text = build()
    with _lock:
        store[key] = (text, now)
    return text


def _drop(store: dict[str, tuple[str, float]], agent_id: str | None) -> int:
    prefix = _tenant_prefix() if agent_id is None else f"{_tenant_prefix()}{agent_id}::"
    keys = [key for key in store if key.startswith(prefix)]
    for key in keys:
        store.pop(key, None)
    return len(keys)
