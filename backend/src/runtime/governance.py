"""三条 Runtime 共用的 Agent 治理公开入口。

单 Agent、TeamRun 和群聊 Worker 都从这里挂上下文 middleware、注入记忆和落会话状态。
Chat 路由里的 `runtime_governance.py` 只做 re-export。新代码不要再从 routes.chat 导入。

约束：本模块不得 import `leader_stream` / `hitl`（群聊工作室有 import 隔离测试）。
"""
from __future__ import annotations


import logging

from src.agents.session_context import restore_session_context_state, snapshot_session_context_state
from src.memory.legacy.injection import MemoryRecallResult
from src.memory.retrieval import build_habit_prompt, build_professional_prompt
from src.server.routes.chat.context_middleware import (
    ContextCompressionMiddleware,
    ContextHygieneMiddleware,
    TimelineRecallMiddleware,
    set_context_session_id,
)
from src.memory.prompt_cache import session_habit_prompt, session_professional_prompt
from src.runtime.tool_loop_guard import ToolLoopGuardMiddleware
from src.server.routes.chat.skill_context_middleware import SkillContextMiddleware
from src.server.routes.chat.trace_recorder import PROMPT_SECTIONS_ATTR
from src.storage.records.session_context_state import (
    load_session_context_state,
    save_session_context_state,
)

logger = logging.getLogger(__name__)


def build_context_governance_middlewares(*, skill_context: bool = True, hard_stop: bool = True) -> list[object]:
    """标准上下文治理 middleware 链：已读 Skill 正文保留（可选）+ 一级无模型整理 + 压缩可观测 + 工具死循环护栏。

    Chat、Team、群聊 Worker 都按工具指纹硬停死循环。`hard_stop=False` 只保留给测试。
    `RunSteerMiddleware` 依赖 Chat Run 的中途补充消息通道，只有单 Agent 路径自己加。
    """

    middlewares: list[object] = []
    if skill_context:
        middlewares.append(SkillContextMiddleware())
    middlewares.append(TimelineRecallMiddleware())
    middlewares.append(ContextHygieneMiddleware())
    middlewares.append(ContextCompressionMiddleware())
    middlewares.append(ToolLoopGuardMiddleware(hard_stop=hard_stop))
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
    project_slug: str,
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

    del agent_name, query_text, exclude_session_id
    if not include_markdown:
        return "", None
    memory_prompt = _compose_frozen_memory_prompt(
        user_id=user_id or "default",
        agent_id=agent_id,
        session_id=freeze_session_id,
        project_slug=project_slug,
    )
    return memory_prompt, None


def build_actor_memory_prompt(
    *,
    user_id: str,
    agent_id: str,
    session_id: str | None,
) -> str:
    """给 Team Member / Subagent 注入共用习惯和它自己的专业全文。"""

    return _compose_frozen_memory_prompt(
        user_id=user_id or "default",
        agent_id=agent_id,
        session_id=session_id,
    )


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


def _compose_frozen_memory_prompt(
    *,
    user_id: str,
    agent_id: str,
    session_id: str | None,
    project_slug: str = "",
) -> str:
    habit = session_habit_prompt(
        user_id=user_id or "default",
        build=lambda: build_habit_prompt(user_id or "default"),
    )
    professional = ""
    if agent_id:
        professional = session_professional_prompt(
            agent_id=agent_id,
            session_id=session_id,
            project_slug=project_slug,
            build=lambda: build_professional_prompt(agent_id),
        )
    return "\n\n".join(part for part in (habit, professional) if part.strip())
