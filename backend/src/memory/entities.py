"""Memory Wiki 轻量实体提取。

首版不引入模型和图数据库，只从已知技术词、记忆概念和上下文实体中提取稳定实体，
用于生成 Obsidian 双链和只读实体索引页。
"""

from __future__ import annotations

import re
from dataclasses import dataclass


_KNOWN_ENTITY_PATTERNS: tuple[tuple[str, str, str], ...] = (
    ("module", "MemoryAdmissionGate", r"\bMemoryAdmissionGate\b"),
    ("module", "MemoryWikiVaultSyncer", r"\bMemoryWikiVaultSyncer\b|Memory Wiki"),
    ("module", "VectorStoreBackend", r"\bVectorStoreBackend\b"),
    ("module", "SkillEvolutionAgent", r"\bSkillEvolutionAgent\b"),
    ("module", "AgentScope", r"\bAgentScope\b"),
    ("storage", "memory_nodes", r"\bmemory_nodes\b"),
    ("storage", "zvec", r"\bzvec\b"),
    ("topic", "历史记忆", r"历史记忆|节点记忆"),
    ("topic", "Markdown 记忆", r"Markdown 记忆|Markdown 长期记忆"),
    ("topic", "Skill 自进化", r"Skill 自进化|技能自进化"),
    ("topic", "Obsidian Wiki", r"Obsidian|Wiki Vault|wiki 视图"),
    ("topic", "hooks", r"\bhooks?\b|hook"),
    ("topic", "tools", r"\btools?\b|工具"),
    ("rule", "Plan Runtime", r"Plan Runtime"),
    ("rule", "Subagent Runtime", r"Subagent Runtime"),
    ("rule", "memory runtime.kind", r"memory runtime\.kind|memory Runtime"),
)

_WIKI_LINK_RE = re.compile(r"\[\[(?:(?P<path>[^\]|]+)\|)?(?P<label>[^\]]+)\]\]")


@dataclass(frozen=True)
class MemoryEntity:
    """Memory Wiki 中的一个稳定实体。"""

    kind: str
    name: str

    @property
    def id(self) -> str:
        return f"{self.kind}:{self.name}"

    @property
    def link(self) -> str:
        return f"[[{self.path}|{_kind_label(self.kind)}:{self.name}]]"

    @property
    def path(self) -> str:
        return f"entities/{_safe_filename(self.kind)}/{_safe_filename(self.name)}"


def make_memory_entity(kind: str, name: str) -> MemoryEntity:
    """创建并规范化实体。"""

    return MemoryEntity(kind=_normalize_kind(kind), name=_normalize_name(name))


def extract_memory_entities(
    text: str,
    *,
    extra_entities: list[MemoryEntity] | None = None,
) -> list[MemoryEntity]:
    """从文本和上下文中提取稳定实体。"""

    entities: dict[str, MemoryEntity] = {}
    for entity in extra_entities or []:
        entities[entity.id] = entity

    for kind, name, pattern in _KNOWN_ENTITY_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            entity = make_memory_entity(kind, name)
            entities[entity.id] = entity

    for match in _WIKI_LINK_RE.finditer(text):
        entity = _entity_from_wiki_link(match.group("label"), match.group("path"))
        if entity is not None:
            entities[entity.id] = entity

    return sorted(entities.values(), key=lambda item: (item.kind, item.name.lower()))


def _normalize_kind(value: str) -> str:
    lowered = value.strip().lower()
    mapping = {
        "agent": "agent",
        "skill": "skill",
        "workspace": "workspace",
        "project": "project",
        "file": "file",
        "tool": "tool",
        "error": "error",
        "user": "user",
        "用户": "user",
        "日期": "date",
        "date": "date",
        "主题": "topic",
        "topic": "topic",
        "项目": "project",
        "文件": "file",
        "工具": "tool",
        "错误": "error",
        "模块": "module",
        "module": "module",
        "规则": "rule",
        "rule": "rule",
        "存储": "storage",
        "storage": "storage",
    }
    return mapping.get(lowered, lowered or "topic")


def _normalize_name(value: str) -> str:
    return " ".join(value.strip().split())[:120] or "unknown"


def _kind_label(kind: str) -> str:
    return {
        "agent": "Agent",
        "skill": "Skill",
        "workspace": "Workspace",
        "project": "项目",
        "file": "文件",
        "tool": "工具",
        "error": "错误",
        "user": "用户",
        "date": "日期",
        "topic": "主题",
        "module": "模块",
        "rule": "规则",
        "storage": "存储",
    }.get(kind, kind)


def _kind_from_label(label: str) -> str:
    return _normalize_kind(label)


def _entity_from_wiki_link(label: str, path: str | None) -> MemoryEntity | None:
    display_label = label.strip()
    if ":" in display_label:
        kind, name = display_label.split(":", 1)
        return make_memory_entity(_kind_from_label(kind), name)

    if path:
        parts = [part for part in path.strip("/").split("/") if part]
        if len(parts) >= 3 and parts[0] == "entities":
            return make_memory_entity(parts[1], parts[-1])

    return None


def _safe_filename(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_.\-\u4e00-\u9fff]+", "_", value.strip())
    return normalized[:120].strip("._-") or "item"
