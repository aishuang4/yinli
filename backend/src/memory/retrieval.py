"""运行前记忆检索与注入文本生成。"""

from __future__ import annotations

from dataclasses import dataclass

from src.memory.markdown_store import MarkdownMemoryStore
from src.memory.nodes import MemoryNodeService

# 注入预算：记忆只是摘要索引，详情靠 memory_node_* 工具按需读取。
# 预算过大既浪费上下文，也会因内容频繁变化破坏供应商 prompt cache。
MEMORY_INJECTION_TOTAL_CHARS = 2500
MEMORY_MARKDOWN_SECTION_CHARS = 700
MEMORY_NODE_TREE_LIMIT = 12
MEMORY_NODE_PER_DAY_LIMIT = 3
MEMORY_NODE_TREE_CHARS = 1000


@dataclass(frozen=True)
class MemoryRetrievalContext:
    """记忆注入所需的运行上下文。"""

    agent_id: str
    agent_name: str
    workspace_id: str = "default"
    project_id: str = "default"
    user_id: str = "default"
    include_user_markdown: bool = True
    include_agent_markdown: bool = True
    include_global_markdown: bool = True
    include_node_tree: bool = True


def build_memory_injection_text(
    context: MemoryRetrievalContext,
    *,
    markdown_store: MarkdownMemoryStore | None = None,
    node_service: MemoryNodeService | None = None,
) -> str:
    """生成可追加到 system prompt 的精简记忆段落。"""

    sections: list[str] = []
    store = markdown_store or MarkdownMemoryStore()

    if context.include_global_markdown:
        markdown = store.read_global_memory(context.workspace_id)
        summary = _extract_markdown_injection(markdown.content)
        if summary:
            sections.append("Global preference Markdown memory:\n" + summary)

    if context.include_user_markdown:
        markdown = store.read_user_memory(context.user_id)
        summary = _extract_markdown_injection(markdown.content)
        if summary:
            sections.append("User Markdown memory:\n" + summary)

    if context.include_agent_markdown:
        markdown = store.read_agent_memory(context.agent_id)
        summary = _extract_markdown_injection(markdown.content)
        if summary:
            sections.append("Agent Markdown memory:\n" + summary)

    if context.include_node_tree:
        tree = (node_service or MemoryNodeService()).tree(
            agent_id=context.agent_id,
            workspace_id=context.workspace_id,
            project_id=context.project_id,
            user_id=context.user_id,
            limit=MEMORY_NODE_TREE_LIMIT * 3,
        )
        node_text = _format_node_tree(tree.get("nodes", []))
        if node_text:
            sections.append("Recent memory nodes (last 30 days, `date | nodeId | title`; use memory_node_query for details):\n" + node_text)

    if not sections:
        return _empty_memory_hint()

    body = _fit_sections_to_budget(sections, MEMORY_INJECTION_TOTAL_CHARS)
    return "\n".join(
        [
            "# Memory",
            "以下是可用长期记忆摘要。若与本轮用户明确要求冲突，以本轮用户消息为准。",
            "这里只注入摘要索引；需要查看某天、某个主题或 nodeId 的详情时，使用 memory_node_tree 或 memory_node_query 工具。",
            "",
            body,
        ],
    )


def _fit_sections_to_budget(sections: list[str], budget: int) -> str:
    """把各段落压进总预算：超出时从最后一段（节点索引）开始裁，再依次向前。"""

    parts = list(sections)
    separator_cost = 2 * max(0, len(parts) - 1)
    total = sum(len(part) for part in parts) + separator_cost
    index = len(parts) - 1
    while total > budget and index >= 0:
        overflow = total - budget
        part = parts[index]
        keep = max(0, len(part) - overflow)
        # 段落至少留一行标题，避免残缺半句
        head = part.split("\n", 1)[0]
        trimmed = part[:keep].rstrip() if keep > len(head) else head
        if trimmed != part:
            trimmed = trimmed + "\n…(已按注入预算截断，详情请用 memory_node_query 查询)"
        total -= len(part) - len(trimmed)
        parts[index] = trimmed
        index -= 1
    return "\n\n".join(parts)


def _empty_memory_hint() -> str:
    return "\n".join(
        [
            "# Memory",
            "当前没有可注入的长期记忆详情。",
            "如果需要查看历史节点，可使用 memory_node_tree 或 memory_node_query 工具。",
        ],
    )


def _extract_markdown_injection(content: str) -> str:
    """从 Markdown 记忆文件抽取注入段落。

    `# Summary` 本身就是其余章节的压缩摘要；存在时只注入 Summary 加上
    直接约束行为的 Corrections / Negative Rules，不再把被摘要过的原始
    章节重抄一遍。没有 Summary 时才逐章节注入。
    """

    sections = _parse_markdown_sections(content)
    summary = _strip_comments(sections.get("Summary", "")).strip()
    if summary:
        titles: tuple[str, ...] = ("Summary", "Corrections", "Negative Rules")
    else:
        titles = (
            "Stable Preferences",
            "Stable Behaviors",
            "Profile",
            "Workspace Rules",
            "Corrections",
            "Negative Rules",
        )
    parts = []
    for title in titles:
        text = _strip_comments(sections.get(title, "")).strip()
        if text:
            parts.append(f"## {title}\n{_trim_block(text, MEMORY_MARKDOWN_SECTION_CHARS)}")
    return "\n\n".join(parts)


def _trim_block(text: str, limit: int) -> str:
    """按字符预算裁多行文本，尽量在行边界截断。"""

    if len(text) <= limit:
        return text
    cut = text.rfind("\n", 0, limit)
    if cut < limit // 2:
        cut = limit
    return text[:cut].rstrip() + "\n…"


def _parse_markdown_sections(content: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current = ""
    for line in content.splitlines():
        if line.startswith("# "):
            current = line[2:].strip()
            sections.setdefault(current, [])
            continue
        if current:
            sections[current].append(line)
    return {
        title: "\n".join(lines).strip()
        for title, lines in sections.items()
    }


def _strip_comments(content: str) -> str:
    lines = [
        line
        for line in content.splitlines()
        if "<!--" not in line and "-->" not in line
    ]
    return "\n".join(lines)


def _format_node_tree(nodes: list[dict]) -> str:
    """把历史记忆节点压成单行索引：按标题去重、每天最多若干条、总量受限。"""

    if not nodes:
        return ""
    lines: list[str] = []
    seen_titles: set[str] = set()
    per_day: dict[str, int] = {}
    for node in nodes:
        if len(lines) >= MEMORY_NODE_TREE_LIMIT:
            break
        title = _trim(node.get("title", ""), 60)
        title_key = " ".join(title.lower().split())
        if title_key and title_key in seen_titles:
            continue
        date = str(node.get("date", "") or "")
        if per_day.get(date, 0) >= MEMORY_NODE_PER_DAY_LIMIT:
            continue
        seen_titles.add(title_key)
        per_day[date] = per_day.get(date, 0) + 1
        # 索引只留日期 / nodeId / 标题：摘要和原文由模型按需通过 memory_node_query 读取，
        # 不在每轮 system prompt 里重复携带。
        lines.append(f"- {date} | {node.get('nodeId', '')} | {title}")
    return _trim_block("\n".join(lines), MEMORY_NODE_TREE_CHARS)


def _trim(value: str, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."
