"""按 runtime agent_id 缓存 Agent 实例。

缓存键不含 system prompt 正文。忙碌或等待用户的实例不淘汰。
同一会话换实例时，对话上下文从旧实例搬到新实例。
"""
from __future__ import annotations


import logging
import time

from agentscope.agent import Agent

from src.agents.session_context import carry_over_context

logger = logging.getLogger(__name__)

# - `_agent_cache`：runtime_agent_id -> 实例。键只包含影响工具集 / 模型 / 权限的稳定配置。
# - `_session_agents`：session_context_key -> 当前 runtime_agent_id。
# - `_agent_last_used`：LRU / 闲置淘汰依据。
_agent_cache: dict[str, Agent] = {}
_session_agents: dict[str, str] = {}
_agent_last_used: dict[str, float] = {}

AGENT_CACHE_MAX_SIZE = 48
AGENT_CACHE_IDLE_SECONDS = 6 * 60 * 60
_BUSY_ATTR = "_aw_busy_run_id"


def _cache_key(key: str) -> str:
    """缓存键带上租户，避免不同租户的 default Agent 共用实例。"""

    from src.tenancy.context import current_tenant

    prefix = f"{current_tenant().tenant_id}:"
    return key if key.startswith(prefix) else f"{prefix}{key}"


def _touch_agent(agent_id: str) -> None:
    _agent_last_used[agent_id] = time.monotonic()


def _drop_agent(agent_id: str) -> None:
    _agent_cache.pop(agent_id, None)
    _agent_last_used.pop(agent_id, None)


def _adopt_session_context(session_context_key: str, agent_id: str, agent: Agent) -> None:
    """同一会话换实例：搬运对话上下文并释放旧实例。"""

    previous_id = _session_agents.get(session_context_key)
    _session_agents[session_context_key] = agent_id
    if not previous_id or previous_id == agent_id:
        return
    previous = _agent_cache.get(previous_id)
    if previous is None:
        return
    if getattr(previous, _BUSY_ATTR, None):
        # 旧实例仍在执行：只复制快照，不动它的状态，也不释放它。
        carry_over_context(previous, agent, settle=False)
        return
    moved = carry_over_context(previous, agent)
    logger.debug("会话 %s 换 Agent 实例，搬运 %s 条上下文", session_context_key, moved)
    _drop_agent(previous_id)


def _agent_is_pinned(agent: Agent) -> bool:
    """正在执行或等待用户回应的实例不淘汰。"""

    if getattr(agent, _BUSY_ATTR, None):
        return True
    state = getattr(agent, "state", None)
    try:
        return bool(state is not None and state.has_awaiting_tool_calls(agent.name))
    except Exception:
        return False


def _evict_idle_agents(
    *,
    now: float | None = None,
    max_size: int = AGENT_CACHE_MAX_SIZE,
    idle_seconds: float = AGENT_CACHE_IDLE_SECONDS,
) -> list[str]:
    """按闲置时长和 LRU 上限淘汰实例；不被任何会话引用的旧实例优先。"""

    current = time.monotonic() if now is None else now
    active_ids = set(_session_agents.values())
    evicted: list[str] = []
    for agent_id, last_used in list(_agent_last_used.items()):
        agent = _agent_cache.get(agent_id)
        if agent is None:
            _agent_last_used.pop(agent_id, None)
            continue
        if current - last_used >= idle_seconds and not _agent_is_pinned(agent):
            evicted.append(agent_id)
    if len(_agent_cache) - len(evicted) > max_size:
        ordered = sorted(_agent_last_used.items(), key=lambda item: item[1])
        for agent_id, _ in ordered:
            if len(_agent_cache) - len(evicted) <= max_size:
                break
            if agent_id in evicted:
                continue
            agent = _agent_cache.get(agent_id)
            if agent is None or _agent_is_pinned(agent):
                continue
            # 仍是某会话当前实例的，最后才考虑
            if agent_id in active_ids and len(_agent_cache) - len(evicted) <= max_size * 2:
                continue
            evicted.append(agent_id)
    for agent_id in evicted:
        _drop_agent(agent_id)
    if evicted:
        for key, value in list(_session_agents.items()):
            if value in evicted:
                _session_agents.pop(key, None)
        logger.debug("淘汰 %s 个闲置 Agent 实例", len(evicted))
    return evicted


def mark_agent_busy(agent: Agent, run_id: str) -> None:
    """标记实例正在执行某个 Run，期间不复用、不淘汰。"""

    setattr(agent, _BUSY_ATTR, run_id or "running")


def mark_agent_idle(agent: Agent) -> None:
    """Run 结束（含暂停等待用户）后解除执行标记。"""

    if hasattr(agent, _BUSY_ATTR):
        try:
            delattr(agent, _BUSY_ATTR)
        except AttributeError:
            pass


def has_cached_agent(agent_id: str) -> bool:
    """运行时缓存里是否已有这个 Agent。"""

    return _cache_key(agent_id) in _agent_cache


def invalidate_agent(agent_id: str) -> None:
    """清掉这个 Agent 及其 fork 缓存。下次请求重新创建。会话映射保留。"""

    agent_id = _cache_key(agent_id)
    keys_to_remove = [
        key
        for key in _agent_cache
        if key == agent_id or key.startswith(f"{agent_id}:")
    ]
    for key in keys_to_remove:
        _drop_agent(key)
    for key, value in list(_session_agents.items()):
        if value in keys_to_remove:
            _session_agents.pop(key, None)


def clear_all_agents() -> None:
    """清除所有 Agent 缓存。"""

    _agent_cache.clear()
    _session_agents.clear()
    _agent_last_used.clear()


def remember_agent(agent_id: str, agent: Agent, session_context_key: str | None) -> None:
    """放入缓存，必要时搬运会话上下文，然后淘汰闲置实例。"""

    _agent_cache[agent_id] = agent
    _touch_agent(agent_id)
    if session_context_key:
        _adopt_session_context(session_context_key, agent_id, agent)
    _evict_idle_agents()
