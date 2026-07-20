"""运行前记忆检索与注入文本生成。"""

from __future__ import annotations

from dataclasses import dataclass

from src.memory.markdown_store import MarkdownMemoryStore
from src.memory.nodes import MemoryNodeService


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
            limit=20,
        )
        node_text = _format_node_tree(tree.get("nodes", []))
        if node_text:
            sections.append("Recent memory node tree, last 30 days:\n" + node_text)

    if not sections:
        return _empty_memory_hint()

    return "\n".join(
        [
            "# Memory",
            "以下是可用长期记忆摘要。若与本轮用户明确要求冲突，以本轮用户消息为准。",
            "这里只注入摘要索引；需要查看某天、某个主题或 nodeId 的详情时，使用 memory_node_tree 或 memory_node_query 工具。",
            "",
            "\n\n".join(sections),
        ],
    )


def _empty_memory_hint() -> str:
    return "\n".join(
        [
            "# Memory",
            "当前没有可注入的长期记忆详情。",
            "如果需要查看历史节点，可使用 memory_node_tree 或 memory_node_query 工具。",
        ],
    )


def _extract_markdown_injection(content: str) -> str:
    sections = _parse_markdown_sections(content)
    parts = []
    for title in (
        "Summary",
        "Stable Preferences",
        "Stable Behaviors",
        "Profile",
        "Workspace Rules",
        "Corrections",
        "Negative Rules",
    ):
        text = _strip_comments(sections.get(title, "")).strip()
        if text:
            parts.append(f"## {title}\n{text}")
    return "\n\n".join(parts)


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
    if not nodes:
        return ""
    lines: list[str] = []
    for node in nodes[:20]:
        tags = ",".join(node.get("tags") or [])
        lines.extend(
            [
                f"- {node.get('date', '')}",
                f"  - agent: {node.get('agentId', '')}",
                f"    nodeId: {node.get('nodeId', '')}",
                f"    title: {_trim(node.get('title', ''), 80)}",
                f"    summary: {_trim(node.get('summary', ''), 180)}",
                f"    tags: {tags}",
            ],
        )
    return "\n".join(lines)


def _trim(value: str, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."
