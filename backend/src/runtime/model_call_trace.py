"""非单 Agent 路径的模型调用观测。"""
from __future__ import annotations


from typing import Any

from src.server.routes.chat.trace_recorder import (
    _record_run_event,
    apply_model_call_token_usage,
    build_model_call_context_stats_async,
    record_context_compression_events,
)


async def observe_model_call_event(
    *,
    agent: object,
    event_type: str,
    event_data: dict[str, Any],
    session_id: str,
    persist: bool,
    scope: str,
    agent_id: str,
    agent_name: str,
) -> dict[str, Any]:
    """非单 Agent 路径的 MODEL_CALL_START / END 观测：写 CONTEXT_COMPRESSED 与 CONTEXT_USAGE trace。

    返回本次统计，调用方可选择再投影成自己的领域事件。非模型调用事件直接返回空 dict。
    """

    if event_type not in {"MODEL_CALL_START", "MODEL_CALL_END"}:
        return {}
    record_context_compression_events(
        agent=agent,
        session_id=session_id,
        persist=persist,
        scope=scope,
        agent_id=agent_id,
        agent_name=agent_name,
    )
    try:
        stats = await build_model_call_context_stats_async(agent)
    except Exception:
        return {}
    if event_type == "MODEL_CALL_END":
        apply_model_call_token_usage(stats, event_data)
    if not stats:
        return {}
    used = int(stats.get("inputTokens") or stats.get("estimatedTokens") or 0)
    window = int(stats.get("contextWindow") or 0)
    _record_run_event(
        session_id=session_id,
        event_type="CONTEXT_USAGE",
        persist=persist,
        scope=scope,
        agent_id=agent_id,
        agent_name=agent_name,
        status="completed" if event_type == "MODEL_CALL_END" else "running",
        title=f"Context usage {used}" + (f" / {window}" if window else ""),
        payload=dict(stats),
    )
    return stats
