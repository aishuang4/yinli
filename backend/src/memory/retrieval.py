"""本轮注入的习惯全文和专业区全文。"""

from __future__ import annotations

from src.memory.markdown_store import MarkdownMemoryStore

_TIMELINE_HINT = (
    "需要回忆时间线时先调用 timeline_retrieve，再用 timeline_ingest 留下这次任务要用的内容。"
    "不要把检索原文留在后续回答里。"
)


def build_habit_prompt(
    user_id: str = "default",
    *,
    markdown_store: MarkdownMemoryStore | None = None,
) -> str:
    """只生成习惯全文和查时间线的提示。"""

    store = markdown_store or MarkdownMemoryStore()
    habits = store.read_habits(user_id).content.strip()
    sections = []
    if habits:
        sections.append("# Habits\n" + habits)
    sections.append(_TIMELINE_HINT)
    return "\n\n".join(sections)


def build_professional_prompt(
    agent_id: str,
    *,
    markdown_store: MarkdownMemoryStore | None = None,
) -> str:
    """只生成这个智能体的专业全文。没有内容时返回空字符串。"""

    if not agent_id:
        return ""
    store = markdown_store or MarkdownMemoryStore()
    professional = store.read_professional(agent_id).content.strip()
    if not professional:
        return ""
    return "# Professional\n" + professional


def build_habit_and_professional_prompt(
    *,
    user_id: str = "default",
    agent_id: str = "",
    include_professional: bool = True,
    markdown_store: MarkdownMemoryStore | None = None,
) -> str:
    """拼上习惯全文和当前智能体专业全文。不包含时间线原文。"""

    sections = [build_habit_prompt(user_id, markdown_store=markdown_store)]
    if include_professional:
        professional = build_professional_prompt(agent_id, markdown_store=markdown_store)
        if professional:
            sections.append(professional)
    return "\n\n".join(sections)
