"""Obsidian-compatible Memory Vault 同步器。

从当前权威存储生成可读 Markdown wiki 视图。该视图用于浏览、导出和后续图谱展示，
不作为运行时事实源，也不反向写回 SQLite / Markdown 长期记忆 / Skills。
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.memory.entities import MemoryEntity, extract_memory_entities, make_memory_entity
from src.memory.markdown_store import MarkdownMemoryStore
from src.config.runtime_paths import MEMORY_ROOT
from src.storage.database import list_memory_nodes, list_skills


@dataclass(frozen=True)
class WikiVaultSyncResult:
    """Wiki vault 同步结果。"""

    path: str
    files_written: int
    markdown_memories: int
    history_nodes: int
    skills: int
    entities: int
    files: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class WikiVaultGraphNode:
    """Wiki vault 图谱节点。"""

    id: str
    kind: str
    label: str
    path: str
    documents: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class WikiVaultGraphEdge:
    """Wiki vault 图谱边。"""

    id: str
    source: str
    target: str
    weight: int
    documents: list[str] = field(default_factory=list)
    type: str = "co_occurs"


@dataclass(frozen=True)
class WikiVaultGraphResult:
    """Wiki vault 图谱响应数据。"""

    nodes: list[WikiVaultGraphNode]
    edges: list[WikiVaultGraphEdge]
    documents: int
    history_limit: int


class MemoryWikiVaultSyncer:
    """把权威记忆源同步为 Obsidian-compatible vault。"""

    def __init__(
        self,
        *,
        memory_store: MarkdownMemoryStore | None = None,
        wiki_root: Path | None = None,
    ) -> None:
        self.memory_store = memory_store or MarkdownMemoryStore()
        self.wiki_root = wiki_root or MEMORY_ROOT / "wiki"
        self._entities: dict[str, MemoryEntity] = {}
        self._entity_backlinks: dict[str, set[str]] = defaultdict(set)
        self._entity_edges: Counter[tuple[str, str]] = Counter()
        self._entity_edge_documents: dict[tuple[str, str], set[str]] = defaultdict(set)

    def sync(self, *, history_limit: int = 200) -> WikiVaultSyncResult:
        """生成 wiki 视图，并返回写入统计。"""

        written: list[Path] = []
        self._entities.clear()
        self._entity_backlinks.clear()
        self._entity_edges.clear()
        self._entity_edge_documents.clear()
        self.wiki_root.mkdir(parents=True, exist_ok=True)

        written.append(self._write_readme())
        memory_count = self._sync_markdown_memories(written)
        history_count = self._sync_history_nodes(written, history_limit=history_limit)
        skill_count = self._sync_skills(written)
        entity_count = self._sync_entities(written)

        return WikiVaultSyncResult(
            path=str(self.wiki_root),
            files_written=len(written),
            markdown_memories=memory_count,
            history_nodes=history_count,
            skills=skill_count,
            entities=entity_count,
            files=[str(path.relative_to(self.wiki_root)) for path in written],
        )

    def graph(self, *, history_limit: int = 200, edge_limit: int = 300) -> WikiVaultGraphResult:
        """从权威记忆源生成结构化实体图谱，不写入 wiki 文件。"""

        self._entities.clear()
        self._entity_backlinks.clear()
        self._entity_edges.clear()
        self._entity_edge_documents.clear()

        documents = self._index_graph_sources(history_limit=history_limit)
        nodes = [
            WikiVaultGraphNode(
                id=entity.id,
                kind=entity.kind,
                label=entity.name,
                path=f"{entity.path}.md",
                documents=sorted(self._entity_backlinks.get(entity.id, set())),
            )
            for entity in sorted(self._entities.values(), key=lambda item: (item.kind, item.name.lower()))
        ]
        edges = [
            WikiVaultGraphEdge(
                id=f"{left}__{right}",
                source=left,
                target=right,
                weight=weight,
                documents=sorted(self._entity_edge_documents.get((left, right), set())),
            )
            for (left, right), weight in self._entity_edges.most_common(edge_limit)
        ]
        return WikiVaultGraphResult(
            nodes=nodes,
            edges=edges,
            documents=documents,
            history_limit=history_limit,
        )

    def _write_readme(self) -> Path:
        path = self.wiki_root / "README.md"
        content = "\n".join(
            [
                "# Agent Workstation Memory Wiki",
                "",
                "这是由系统从权威记忆源生成的 Obsidian-compatible vault 视图。",
                "",
                "- `users/`：用户长期记忆。",
                "- `agents/`：各 Agent 的迭代记忆。",
                "- `global/`：全局偏好和工作区规则。",
                "- `history/`：按 Agent 和日期组织的历史记忆。",
                "- `skills/`：Skill 自进化和用户注册后的标准 Skill 视图。",
                "- `entities/`：从记忆和 Skill 中提取的只读实体索引与共现图。",
                "",
                "注意：当前 wiki 是只读生成视图。运行时仍以 SQLite、Markdown 记忆文件和 VectorStoreBackend 为事实源。",
                "",
            ]
        )
        return self._write(path, content)

    def _sync_markdown_memories(self, written: list[Path]) -> int:
        count = 0
        for source, target, title, forced_entities in self._iter_existing_markdown_memories():
            content = source.read_text(encoding="utf-8")
            links = self._entity_links_for_document(target, content, forced_entities)
            wiki_content = _frontmatter({
                "source": str(source),
                "kind": "markdown_memory",
                "title": title,
            }) + f"# {title}\n\n关联：{' '.join(links)}\n\n" + content.strip() + "\n"
            written.append(self._write(target, wiki_content))
            count += 1
        return count

    def _iter_existing_markdown_memories(self):
        root = self.memory_store.root
        user_default = root / "users" / "user_memory.md"
        if user_default.exists():
            yield (
                user_default,
                self.wiki_root / "users" / "user_memory.md",
                "用户记忆",
                [make_memory_entity("user", "default")],
            )
        for path in sorted((root / "users").glob("*/user_memory.md")):
            owner_id = path.parent.name
            yield (
                path,
                self.wiki_root / "users" / _safe_filename(owner_id) / "user_memory.md",
                f"用户记忆 {owner_id}",
                [make_memory_entity("user", owner_id)],
            )

        agent_default = root / "agents" / "agent_memory.md"
        if agent_default.exists():
            yield (
                agent_default,
                self.wiki_root / "agents" / "default" / "agent_memory.md",
                "Agent 记忆 default",
                [make_memory_entity("agent", "default")],
            )
        for path in sorted((root / "agents").glob("*/agent_memory.md")):
            owner_id = path.parent.name
            yield (
                path,
                self.wiki_root / "agents" / _safe_filename(owner_id) / "agent_memory.md",
                f"Agent 记忆 {owner_id}",
                [make_memory_entity("agent", owner_id)],
            )

        global_default = root / "global" / "global_memory.md"
        if global_default.exists():
            yield (
                global_default,
                self.wiki_root / "global" / "global_memory.md",
                "全局记忆",
                [make_memory_entity("workspace", "default")],
            )
        for path in sorted((root / "global").glob("*/global_memory.md")):
            owner_id = path.parent.name
            yield (
                path,
                self.wiki_root / "global" / _safe_filename(owner_id) / "global_memory.md",
                f"全局记忆 {owner_id}",
                [make_memory_entity("workspace", owner_id)],
            )

    def _sync_history_nodes(self, written: list[Path], *, history_limit: int) -> int:
        nodes = list_memory_nodes(limit=history_limit)
        for node in nodes:
            agent_id = str(node.get("agent_id") or "unknown")
            node_date = str(node.get("node_date") or "unknown-date")
            node_id = str(node.get("id") or "")
            title = str(node.get("title") or node_id or "历史记忆")
            path = (
                self.wiki_root
                / "history"
                / _safe_filename(agent_id)
                / f"{_safe_filename(node_date)}-{_safe_filename(node_id or title)}.md"
            )
            forced_entities = [
                make_memory_entity("agent", agent_id),
                make_memory_entity("date", node_date),
            ]
            extracted_links = self._entity_links_for_document(
                path,
                _history_node_plain_text(node),
                forced_entities,
            )
            content = _history_node_markdown(node, entity_links=extracted_links)
            written.append(self._write(path, content))
        return len(nodes)

    def _sync_skills(self, written: list[Path]) -> int:
        skills = list_skills()
        for skill in skills:
            name = str(skill.get("name") or skill.get("id") or "skill")
            path = self.wiki_root / "skills" / f"{_safe_filename(name)}.md"
            forced_entities = [make_memory_entity("skill", name)]
            extracted_links = self._entity_links_for_document(
                path,
                _skill_plain_text(skill),
                forced_entities,
            )
            content = _skill_markdown(skill, entity_links=extracted_links)
            written.append(self._write(path, content))
        return len(skills)

    def _index_graph_sources(self, *, history_limit: int) -> int:
        count = 0
        for source, target, _title, forced_entities in self._iter_existing_markdown_memories():
            self._entity_links_for_document(
                target,
                source.read_text(encoding="utf-8"),
                forced_entities,
            )
            count += 1

        for node in list_memory_nodes(limit=history_limit):
            agent_id = str(node.get("agent_id") or "unknown")
            node_date = str(node.get("node_date") or "unknown-date")
            node_id = str(node.get("id") or "")
            title = str(node.get("title") or node_id or "历史记忆")
            target = (
                self.wiki_root
                / "history"
                / _safe_filename(agent_id)
                / f"{_safe_filename(node_date)}-{_safe_filename(node_id or title)}.md"
            )
            self._entity_links_for_document(
                target,
                _history_node_plain_text(node),
                [
                    make_memory_entity("agent", agent_id),
                    make_memory_entity("date", node_date),
                ],
            )
            count += 1

        for skill in list_skills():
            name = str(skill.get("name") or skill.get("id") or "skill")
            target = self.wiki_root / "skills" / f"{_safe_filename(name)}.md"
            self._entity_links_for_document(
                target,
                _skill_plain_text(skill),
                [make_memory_entity("skill", name)],
            )
            count += 1

        return count

    def _sync_entities(self, written: list[Path]) -> int:
        for entity in sorted(self._entities.values(), key=lambda item: (item.kind, item.name.lower())):
            path = self.wiki_root / "entities" / _safe_filename(entity.kind) / f"{_safe_filename(entity.name)}.md"
            content = _entity_markdown(entity, sorted(self._entity_backlinks.get(entity.id, set())))
            written.append(self._write(path, content))
        if self._entity_edges:
            written.append(self._write(
                self.wiki_root / "entities" / "graph.md",
                _entity_graph_markdown(self._entity_edges, self._entities),
            ))
        return len(self._entities)

    def _entity_links_for_document(
        self,
        target: Path,
        text: str,
        forced_entities: list[MemoryEntity],
    ) -> list[str]:
        entities = extract_memory_entities(text, extra_entities=forced_entities)
        relative = str(target.relative_to(self.wiki_root))
        for entity in entities:
            self._entities[entity.id] = entity
            self._entity_backlinks[entity.id].add(relative)
        for index, left in enumerate(entities):
            for right in entities[index + 1:]:
                key = tuple(sorted([left.id, right.id]))
                self._entity_edges[key] += 1
                self._entity_edge_documents[key].add(relative)
        return [entity.link for entity in entities]

    def _write(self, path: Path, content: str) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content.rstrip() + "\n", encoding="utf-8")
        return path


def _history_node_markdown(node: dict[str, Any], *, entity_links: list[str]) -> str:
    agent_id = str(node.get("agent_id") or "unknown")
    node_date = str(node.get("node_date") or "")
    title = str(node.get("title") or "历史记忆")
    tags = list(node.get("tags") or [])
    lines = [
        _frontmatter({
            "id": str(node.get("id") or ""),
            "kind": "history_memory",
            "agent_id": agent_id,
            "date": node_date,
            "source_run_id": str(node.get("source_run_id") or ""),
            "tags": tags,
        }).rstrip(),
        f"# {title}",
        "",
        f"关联：{' '.join(entity_links)}",
        "",
        "## 摘要",
        "",
        str(node.get("summary") or "").strip() or "暂无摘要。",
    ]
    digest = str(node.get("conversation_digest") or "").strip()
    if digest:
        lines.extend(["", "## 压缩摘要", "", digest])
    user_inputs = list(node.get("user_inputs") or [])
    if user_inputs:
        lines.extend(["", "## 用户输入", ""])
        lines.extend(f"- {item}" for item in user_inputs[:10])
    outputs = list(node.get("agent_final_outputs") or [])
    if outputs:
        lines.extend(["", "## Agent 最终输出", ""])
        lines.extend(f"- {item}" for item in outputs[:10])
    return "\n".join(lines) + "\n"


def _history_node_plain_text(node: dict[str, Any]) -> str:
    return "\n".join(
        [
            str(node.get("title") or ""),
            str(node.get("summary") or ""),
            str(node.get("conversation_digest") or ""),
            "\n".join(str(item) for item in list(node.get("user_inputs") or [])),
            "\n".join(str(item) for item in list(node.get("agent_final_outputs") or [])),
            "\n".join(str(item) for item in list(node.get("tags") or [])),
        ]
    )


def _skill_markdown(skill: dict[str, Any], *, entity_links: list[str]) -> str:
    name = str(skill.get("name") or skill.get("id") or "Skill")
    tags = list(skill.get("tags") or [])
    content = str(skill.get("content") or "").strip()
    lines = [
        _frontmatter({
            "id": str(skill.get("id") or ""),
            "kind": "skill",
            "name": name,
            "category": str(skill.get("category") or ""),
            "enabled": bool(skill.get("enabled")),
            "tags": tags,
        }).rstrip(),
        f"# Skill: {name}",
        "",
        f"关联：{' '.join(entity_links)}",
        "",
        "## 描述",
        "",
        str(skill.get("description") or "").strip() or "暂无描述。",
    ]
    trigger = str(skill.get("trigger") or "").strip()
    if trigger:
        lines.extend(["", "## 触发条件", "", trigger])
    if content:
        lines.extend(["", "## SKILL.md", "", content])
    return "\n".join(lines) + "\n"


def _skill_plain_text(skill: dict[str, Any]) -> str:
    return "\n".join(
        [
            str(skill.get("name") or ""),
            str(skill.get("description") or ""),
            str(skill.get("trigger") or ""),
            str(skill.get("content") or ""),
            "\n".join(str(item) for item in list(skill.get("tags") or [])),
        ]
    )


def _entity_markdown(entity: MemoryEntity, backlinks: list[str]) -> str:
    lines = [
        _frontmatter({
            "id": entity.id,
            "kind": entity.kind,
            "name": entity.name,
        }).rstrip(),
        f"# {entity.link}",
        "",
        "## Backlinks",
        "",
    ]
    if backlinks:
        lines.extend(f"- [[{path}]]" for path in backlinks[:200])
    else:
        lines.append("- 暂无引用。")
    return "\n".join(lines) + "\n"


def _entity_graph_markdown(edges: Counter[tuple[str, str]], entities: dict[str, MemoryEntity]) -> str:
    lines = [
        "# Memory Entity Graph",
        "",
        "共现关系由同一 wiki 文档内出现的实体派生，当前仅用于 Obsidian 浏览。",
        "",
        "| Source | Target | Weight |",
        "| --- | --- | ---: |",
    ]
    for (left, right), weight in edges.most_common(300):
        left_link = entities[left].link if left in entities else f"[[{left}]]"
        right_link = entities[right].link if right in entities else f"[[{right}]]"
        lines.append(f"| {left_link} | {right_link} | {weight} |")
    return "\n".join(lines) + "\n"


def _frontmatter(data: dict[str, Any]) -> str:
    lines = ["---"]
    for key, value in data.items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            for item in value:
                lines.append(f"  - {_yaml_scalar(str(item))}")
        elif isinstance(value, bool):
            lines.append(f"{key}: {'true' if value else 'false'}")
        else:
            lines.append(f"{key}: {_yaml_scalar(str(value))}")
    lines.extend(["---", ""])
    return "\n".join(lines)


def _yaml_scalar(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _safe_filename(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_.\-\u4e00-\u9fff]+", "_", value.strip())
    return normalized[:120].strip("._-") or "item"
