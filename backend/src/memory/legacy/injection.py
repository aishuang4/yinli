"""旧的三层 Markdown 摘要注入和节点自动召回。

运行时不调用这些函数。
"""

from __future__ import annotations

from dataclasses import dataclass

from src.memory.legacy.markdown_files import LegacyMarkdownStore
from src.memory.nodes import MemoryNodeService

# 注入预算：记忆只是摘要索引，详情靠 memory_node_* 工具按需读取。
# 预算过大既浪费上下文，也会因内容频繁变化破坏供应商 prompt cache。
MEMORY_INJECTION_TOTAL_CHARS = 2500
MEMORY_MARKDOWN_SECTION_CHARS = 700
MEMORY_NODE_TREE_LIMIT = 12
MEMORY_NODE_PER_DAY_LIMIT = 3
MEMORY_NODE_TREE_CHARS = 1000
# 运行前按本轮用户消息召回：只取最相关的几条，带一句话摘要，附在用户消息后而不是 system prompt。
MEMORY_RECALL_LIMIT = 4
MEMORY_RECALL_CHARS = 900
# 低于这个排序分的候选不注入：一次词命中约 1.0；只有向量分时需 ≥0.8 才能过（0.8*0.35 + 0.7*0.25）。
MEMORY_RECALL_MIN_SCORE = 0.45
# 用户消息太短（"继续"、"好的"）不做召回，避免噪音。
MEMORY_RECALL_MIN_QUERY_CHARS = 6


@dataclass(frozen=True)
class MemoryRetrievalContext:
    """记忆注入所需的运行上下文。"""

    agent_id: str
    agent_name: str
    project_slug: str = "default"
    user_id: str = "default"
    include_user_markdown: bool = True
    include_agent_markdown: bool = True
    include_global_markdown: bool = True
    include_node_tree: bool = True

def build_memory_injection_text(
    context: MemoryRetrievalContext,
    *,
    markdown_store: LegacyMarkdownStore | None = None,
    node_service: MemoryNodeService | None = None,
) -> str:
    """生成可追加到 system prompt 的精简记忆段落。"""

    sections: list[str] = []
    store = markdown_store or LegacyMarkdownStore()

    if context.include_global_markdown:
        markdown = store.read_global_memory()
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
            project_slug=context.project_slug,
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
            "与本轮消息相关的历史记忆会以 <recalled-memory> 附在用户消息后；"
            "当用户提到\"上次 / 之前 / 以前做过 / 按老规矩\"或需要历史决策、历史产物路径时，"
            "先用 memory_node_query 查询（可按 nodeId、关键词或日期），不要凭印象回答。",
            "",
            body,
        ],
    )


@dataclass(frozen=True)
class MemoryRecallResult:
    """运行前自动召回结果：注入文本 + 审计用元数据。"""

    text: str
    node_ids: list[str]
    candidate_count: int
    query_chars: int


def build_memory_recall(
    context: MemoryRetrievalContext,
    *,
    query_text: str,
    exclude_session_id: str = "",
    node_service: MemoryNodeService | None = None,
    limit: int = MEMORY_RECALL_LIMIT,
) -> MemoryRecallResult:
    """按本轮用户消息召回最相关的历史记忆节点，生成附在用户消息后的 `<recalled-memory>` 段。

    与 system prompt 里的 Markdown 摘要不同，这段随每轮变化，所以不能进 system prompt
    （会打破供应商前缀 cache）。本会话自己产生的节点已经在对话上下文里，排除。
    """

    query = " ".join(str(query_text or "").split())
    empty = MemoryRecallResult(text="", node_ids=[], candidate_count=0, query_chars=len(query))
    if len(query) < MEMORY_RECALL_MIN_QUERY_CHARS:
        return empty
    try:
        payload = (node_service or MemoryNodeService()).query(
            query_text=query,
            agent_id=None,
            limit=limit * 3,
            project_slug=context.project_slug,
            user_id=context.user_id,
            mark_used=False,
        )
    except Exception:
        return empty
    results = list(payload.get("results") or [])
    picked: list[dict] = []
    seen_titles: set[str] = set()
    for node in results:
        if exclude_session_id and str(node.get("sourceSessionId") or "") == exclude_session_id:
            continue
        if float(node.get("rankScore") or 0.0) < MEMORY_RECALL_MIN_SCORE:
            continue
        title_key = " ".join(str(node.get("title") or "").lower().split())
        if title_key in seen_titles:
            continue
        seen_titles.add(title_key)
        picked.append(node)
        if len(picked) >= limit:
            break
    if not picked:
        return MemoryRecallResult(text="", node_ids=[], candidate_count=len(results), query_chars=len(query))
    lines = []
    for node in picked:
        summary = _trim(str(node.get("summary") or ""), 150)
        title = _trim(str(node.get("title") or ""), 50)
        agent_id = str(node.get("agentId") or "")
        who = f" ({agent_id})" if agent_id and agent_id != context.agent_id else ""
        line = f"- {node.get('date', '')} | {node.get('nodeId', '')}{who} | {title}"
        if summary and summary != title:
            line += f" — {summary}"
        lines.append(line)
    body = _trim_block("\n".join(lines), MEMORY_RECALL_CHARS)
    text = "\n".join(
        [
            "<recalled-memory>",
            "以下是与本轮消息相关的历史记忆（自动召回，可能不完全相关；需要原文时用 memory_node_query 按 nodeId 查询）：",
            body,
            "</recalled-memory>",
        ]
    )
    return MemoryRecallResult(
        text=text,
        node_ids=[str(node.get("nodeId") or "") for node in picked],
        candidate_count=len(results),
        query_chars=len(query),
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
