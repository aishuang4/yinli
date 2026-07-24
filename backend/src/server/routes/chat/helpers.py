"""对话路由通用小工具 — 源信号构造、文本裁剪、Skill 状态更新。"""

from src.server.routes.chat.models import ChatSkillRun
from src.server.routes.chat.projection.source_events import SourceEvent


def source_event(event_type: str, data: dict) -> SourceEvent:
    """构造进程内结构化源信号；历史名称将在调用点逐步收口。"""

    return SourceEvent(event_type, data)


def _trim_text(text: str, limit: int) -> str:
    """Trim text for compact activity cards."""
    compact = " ".join(text.strip().split())
    if len(compact) <= limit:
        return compact
    return compact[:limit] + "..."


def _set_skill_run_status(
    skill_runs: list[ChatSkillRun],
    status: str,
) -> list[ChatSkillRun]:
    """返回更新状态后的 Skill run 列表。"""
    return [
        item.model_copy(update={"status": status})
        for item in skill_runs
    ]
