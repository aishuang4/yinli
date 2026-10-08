"""各 Runtime 共用的 Agent 治理组件：上下文 middleware、记忆注入 / 召回、会话状态落库、模型调用观测。

单 Agent ReAct、一次性 TeamRun（Leader / Member / 综合）和群聊工作室（Host / Worker）此前各自建
Agent、各自拼 prompt、各自消费 `reply_stream`，导致上下文治理、Skill 正文保留、记忆注入只在单 Agent
路径生效。这里把可复用的部分收成函数，三条路径都调这里，不再各写一份。

约束：本模块不得 import `leader_stream` / `hitl`（群聊工作室有 import 隔离测试）。
"""

from __future__ import annotations

import logging
from typing import Any

from src.agents.session_context import restore_session_context_state, snapshot_session_context_state
from src.memory.retrieval import (
    MemoryRecallResult,
    build_habit_prompt,
    build_professional_prompt,
)
from src.memory.trace import record_memory_audit_event
from src.server.routes.chat.context_middleware import (
    ContextCompressionMiddleware,
    ContextHygieneMiddleware,
    TimelineRecallMiddleware,
    set_context_session_id,
)
from src.memory.prompt_cache import session_habit_prompt, session_professional_prompt
from src.server.routes.chat.skill_context_middleware import SkillContextMiddleware
from src.server.routes.chat.trace_recorder import (
    PROMPT_SECTIONS_ATTR,
    _record_run_event,
    apply_model_call_token_usage,
    build_model_call_context_stats_async,
    record_context_compression_events,
)
from src.storage.database import load_session_context_state, save_session_context_state

logger = logging.getLogger(__name__)


def build_context_governance_middlewares(*, skill_context: bool = True) -> list[object]:
    """标准上下文治理 middleware 链：已读 Skill 正文保留（可选）+ 一级无模型整理 + 压缩可观测。

    `RunSteerMiddleware` 依赖 Chat Run 的中途补充消息通道，只有单 Agent 路径自己加。
    """

    middlewares: list[object] = []
    if skill_context:
        middlewares.append(SkillContextMiddleware())
    middlewares.append(TimelineRecallMiddleware())
    middlewares.append(ContextHygieneMiddleware())
    middlewares.append(ContextCompressionMiddleware())
    return middlewares


def prepare_agent_governance(agent: object, *, session_id: str, prompt_sections: dict[str, int] | None = None) -> None:
    """实例创建 / 复用后：offloader 按会话分目录，并挂上 prompt 分段字数供 contextStats 使用。"""

    set_context_session_id(agent, session_id)
    if prompt_sections is not None:
        try:
            setattr(agent, PROMPT_SECTIONS_ATTR, dict(prompt_sections))
        except Exception:
            pass


def restore_agent_session_state(agent: object, session_id: str) -> bool:
    """进程内没有会话上下文时，从 SQLite 恢复压缩摘要与 middleware 跨轮数据。"""

    if not session_id:
        return False
    try:
        record = load_session_context_state(session_id)
    except Exception:
        logger.debug("读取会话上下文状态失败", exc_info=True)
        return False
    if not record:
        return False
    return restore_session_context_state(
        agent,
        str(record.get("summary") or ""),
        record.get("middleContext") if isinstance(record.get("middleContext"), dict) else None,
    )


def persist_agent_session_state(agent: object, *, session_id: str, agent_id: str) -> None:
    """每轮结束把压缩摘要与 middleware 跨轮数据落库；失败不影响主流程。"""

    if not session_id:
        return
    try:
        summary, middle_context = snapshot_session_context_state(agent)
        save_session_context_state(
            session_id=session_id,
            agent_id=agent_id,
            summary=summary,
            middle_context=middle_context,
        )
    except Exception:
        logger.debug("保存会话上下文状态失败", exc_info=True)


def build_memory_prompt_and_recall(
    *,
    agent_id: str,
    agent_name: str,
    workspace_id: str,
    project_id: str,
    query_text: str,
    freeze_session_id: str | None,
    exclude_session_id: str = "",
    include_markdown: bool = True,
    user_id: str = "default",
) -> tuple[str, MemoryRecallResult | None]:
    """返回 (进 system prompt 的习惯全文 + 专业全文, 空召回)。

    时间线不再自动注入。`query_text` / `exclude_session_id` 保留签名，避免旧调用点立刻崩掉。
    习惯按用户冻结，专业全文按智能体和会话冻结。
    """

    del agent_name, workspace_id, project_id, query_text, exclude_session_id
    if not include_markdown:
        return "", None
    memory_prompt = _compose_frozen_memory_prompt(
        user_id=user_id,
        agent_id=agent_id,
        session_id=freeze_session_id,
    )
    return memory_prompt, None


def build_actor_memory_prompt(
    *,
    user_id: str,
    agent_id: str,
    session_id: str | None,
) -> str:
    """给 Team Member / Subagent 注入共用习惯和它自己的专业全文。"""

    return _compose_frozen_memory_prompt(user_id=user_id, agent_id=agent_id, session_id=session_id)


def refresh_context_memory_prompt(ctx: object, memory_prompt: str) -> None:
    """习惯写入后重拼这一轮的 system prompt，让这次回复用上新全文。"""

    old = str(getattr(ctx, "memory_prompt", "") or "")
    system_prompt = str(getattr(ctx, "system_prompt", "") or "")
    if old and old in system_prompt:
        system_prompt = system_prompt.replace(old, memory_prompt, 1)
    elif memory_prompt:
        system_prompt = f"{system_prompt}\n\n{memory_prompt}" if system_prompt else memory_prompt
    setattr(ctx, "system_prompt", system_prompt)
    setattr(ctx, "memory_prompt", memory_prompt)
    sections = dict(getattr(ctx, "system_prompt_sections", {}) or {})
    sections["memory"] = len(memory_prompt)
    setattr(ctx, "system_prompt_sections", sections)


def _compose_frozen_memory_prompt(*, user_id: str, agent_id: str, session_id: str | None) -> str:
    habit = session_habit_prompt(
        user_id=user_id or "default",
        build=lambda: build_habit_prompt(user_id or "default"),
    )
    professional = ""
    if agent_id:
        professional = session_professional_prompt(
            agent_id=agent_id,
            session_id=session_id,
            build=lambda: build_professional_prompt(agent_id),
        )
    return "\n\n".join(part for part in (habit, professional) if part.strip())


def record_memory_recall_audit(
    *,
    session_id: str,
    persist: bool,
    agent_id: str,
    agent_name: str,
    recall: MemoryRecallResult,
    scope_label: str = "",
) -> None:
    """把自动召回写进 MEMORY_AUDIT：命中哪些 nodeId、候选多少、是否注入。"""

    prefix = f"[{scope_label}] " if scope_label else ""
    record_memory_audit_event(
        session_id=session_id,
        persist=persist,
        agent_id=agent_id,
        agent_name=agent_name,
        action="memory_recall",
        status="completed",
        title=f"{prefix}Memory recalled for turn" if recall.node_ids else f"{prefix}Memory recall found nothing relevant",
        content=f"injected={len(recall.node_ids)}, candidates={recall.candidate_count}",
        payload={
            "nodeIds": recall.node_ids,
            "candidateCount": recall.candidate_count,
            "queryChars": recall.query_chars,
            "injectedChars": len(recall.text),
        },
    )


def append_user_note(text: str, note: str) -> str:
    """把附件说明 / 召回记忆等附加段拼到用户文本后面。"""

    if not note:
        return text
    return f"{text}\n\n{note}" if text else note


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
