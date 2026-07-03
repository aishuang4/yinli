"""Markdown 长期记忆文件存储。

记忆文件采用懒创建：读取不存在的记忆时返回默认模板但不落盘；首次保存时才创建文件。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from src.storage.database import BACKEND_ROOT

AGENT_MEMORY_TEMPLATE = """# Summary

<!-- Agent 迭代记忆摘要。 -->

# Stable Behaviors

<!-- 这个 Agent 应长期保持的工作方式。 -->

# Corrections

<!-- 用户纠正、反复犯错点和修正规则。 -->

# Negative Rules

<!-- 明确禁止或应避免的做法，优先级高于普通偏好。 -->

# Source References

<!-- 来源 session/run/message 引用，只保留短引用，不复制长对话。 -->
"""


@dataclass(frozen=True)
class MarkdownMemoryDocument:
    """Markdown 记忆读取结果。"""

    scope: str
    owner_id: str
    path: str
    content: str
    exists: bool


class MarkdownMemoryStore:
    """Markdown 记忆文件存储。"""

    def __init__(self, root: Path | None = None):
        self.root = root or BACKEND_ROOT / "data" / "memory"

    def read_agent_memory(self, agent_id: str) -> MarkdownMemoryDocument:
        """读取 Agent 记忆；不存在时返回默认模板但不创建文件。"""

        path = self._agent_path(agent_id)
        if path.exists():
            content = path.read_text(encoding="utf-8")
            exists = True
        else:
            content = AGENT_MEMORY_TEMPLATE
            exists = False
        return MarkdownMemoryDocument(
            scope="agent",
            owner_id=agent_id,
            path=str(path),
            content=content,
            exists=exists,
        )

    def write_agent_memory(self, agent_id: str, content: str) -> MarkdownMemoryDocument:
        """保存 Agent 记忆；首次写入时创建文件。"""

        path = self._agent_path(agent_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        normalized = content.rstrip() + "\n"
        path.write_text(normalized, encoding="utf-8")
        return MarkdownMemoryDocument(
            scope="agent",
            owner_id=agent_id,
            path=str(path),
            content=normalized,
            exists=True,
        )

    def _agent_path(self, agent_id: str) -> Path:
        return self.root / "agents" / f"{_safe_id(agent_id)}.md"


def _safe_id(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_.-]+", "_", value.strip())
    return normalized[:120] or "default"
