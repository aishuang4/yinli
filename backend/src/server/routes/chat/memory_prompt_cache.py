"""按会话冻结的记忆注入文本。

记忆摘要（Markdown 章节 + 最近历史记忆索引）位于 system prompt 开头附近。每轮成功
结束后后台会持久化一条新的历史记忆节点，若下一轮重新生成注入文本，system prompt
就会变化几十个字符，供应商的前缀 prompt cache 在每轮第一次模型调用上必然失效
（桌面端轨迹：同会话 4 个新轮次首个调用 `cache_input_tokens` 全为 0）。

这里把注入文本按 `(agent_id, session_id)` 冻结：会话内后续轮次复用第一次生成的
文本，system prompt 保持字节一致。本会话新产生的记忆已经在 AgentScope `state.context`
里，模型也有 `memory_node_query` 工具按需查询，会话内不刷新摘要没有信息损失。

失效时机：TTL 到期（与 Agent 实例闲置淘汰一致）、Memory 页面手动改写 Markdown /
节点状态、进程重启（实例上下文本来也要重建）。后台自动提炼不触发失效。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from src.agents.session_context import session_context_key

_lock = threading.Lock()
_cache: dict[str, tuple[str, float]] = {}
# 与 factory.AGENT_CACHE_IDLE_SECONDS 保持一致（6h）；不直接 import 以免 memory 路由拖入工厂。
MEMORY_PROMPT_TTL_SECONDS = 6 * 60 * 60


def session_memory_prompt(
    *,
    agent_id: str,
    session_id: str | None,
    build: Callable[[], str],
) -> str:
    """返回该会话冻结的记忆注入文本；首次（或失效后）调用 `build()` 生成。"""

    if not session_id or not agent_id:
        return build()
    key = session_context_key(agent_id, session_id)
    now = time.monotonic()
    with _lock:
        cached = _cache.get(key)
        if cached is not None and now - cached[1] < MEMORY_PROMPT_TTL_SECONDS:
            _cache[key] = (cached[0], now)
            return cached[0]
    text = build()
    with _lock:
        _cache[key] = (text, now)
    return text


def invalidate_memory_prompts(*, agent_id: str | None = None) -> int:
    """让冻结的记忆文本失效；不传 `agent_id` 时全部失效。返回清除条数。"""

    with _lock:
        if agent_id is None:
            count = len(_cache)
            _cache.clear()
            return count
        prefix = f"{agent_id}::"
        keys = [key for key in _cache if key.startswith(prefix)]
        for key in keys:
            _cache.pop(key, None)
        return len(keys)
