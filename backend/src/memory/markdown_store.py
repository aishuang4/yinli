"""Markdown 长期记忆文件存储。

记忆文件采用懒创建：读取不存在的记忆时返回默认模板但不落盘；首次保存时才创建文件。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from src.config.runtime_paths import memory_root

USER_MEMORY_TEMPLATE = """# Summary

# Stable Preferences

# Profile

# Corrections

# Negative Rules

# Source References
"""


AGENT_MEMORY_TEMPLATE = """# Summary

# Stable Behaviors

# Corrections

# Negative Rules

# Source References
"""


GLOBAL_MEMORY_TEMPLATE = """# Summary

# Stable Preferences

# Workspace Rules

# Corrections

# Negative Rules

# Source References
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
        self.root = root or memory_root()

    def read_agent_memory(self, agent_id: str) -> MarkdownMemoryDocument:
        """读取 Agent 记忆；不存在时返回默认模板但不创建文件。"""

        return self._read_document(
            scope="agent",
            owner_id=agent_id,
            path=self._agent_path(agent_id),
            template=AGENT_MEMORY_TEMPLATE,
        )

    def write_agent_memory(self, agent_id: str, content: str) -> MarkdownMemoryDocument:
        """保存 Agent 记忆；首次写入时创建文件。"""

        return self._write_document(
            scope="agent",
            owner_id=agent_id,
            path=self._agent_path(agent_id),
            content=content,
        )

    def read_user_memory(self, user_id: str = "default") -> MarkdownMemoryDocument:
        """读取用户单独记忆；不存在时返回默认模板但不创建文件。"""

        return self._read_document(
            scope="user",
            owner_id=user_id,
            path=self._user_path(user_id),
            template=USER_MEMORY_TEMPLATE,
        )

    def write_user_memory(self, user_id: str, content: str) -> MarkdownMemoryDocument:
        """保存用户单独记忆；首次写入时创建文件。"""

        return self._write_document(
            scope="user",
            owner_id=user_id,
            path=self._user_path(user_id),
            content=content,
        )

    def read_global_memory(self) -> MarkdownMemoryDocument:
        """读取租户全局偏好记忆；不存在时返回默认模板但不创建文件。

        路径跟 ``tenant_root()`` 走，不再用 workspace 当 owner。
        """

        return self._read_document(
            scope="global_preference",
            owner_id="tenant",
            path=self._global_path(),
            template=GLOBAL_MEMORY_TEMPLATE,
        )

    def write_global_memory(self, content: str) -> MarkdownMemoryDocument:
        """保存租户全局偏好记忆；首次写入时创建文件。"""

        return self._write_document(
            scope="global_preference",
            owner_id="tenant",
            path=self._global_path(),
            content=content,
        )

    def read_habits(self, user_id: str = "default") -> MarkdownMemoryDocument:
        """读取这个人的习惯全文；不存在时返回空内容且不创建文件。"""

        return self._read_document(
            scope="habits",
            owner_id=user_id,
            path=self._habits_path(user_id),
            template="",
        )

    def write_habits(self, user_id: str, content: str) -> MarkdownMemoryDocument:
        """覆盖保存习惯全文。"""

        return self._write_document(
            scope="habits",
            owner_id=user_id,
            path=self._habits_path(user_id),
            content=content,
        )

    def read_professional(self, agent_id: str) -> MarkdownMemoryDocument:
        """读取某个智能体的专业区全文；不存在时返回空内容且不创建文件。"""

        return self._read_document(
            scope="professional",
            owner_id=agent_id,
            path=self._professional_path(agent_id),
            template="",
        )

    def write_professional(self, agent_id: str, content: str) -> MarkdownMemoryDocument:
        """覆盖保存专业区全文。允许比旧文更短，用来删除过时段落。"""

        return self._write_document(
            scope="professional",
            owner_id=agent_id,
            path=self._professional_path(agent_id),
            content=content,
        )

    def _read_document(
        self,
        *,
        scope: str,
        owner_id: str,
        path: Path,
        template: str,
    ) -> MarkdownMemoryDocument:
        if path.exists():
            content = path.read_text(encoding="utf-8")
            exists = True
        else:
            content = template
            exists = False
        return MarkdownMemoryDocument(
            scope=scope,
            owner_id=owner_id,
            path=str(path),
            content=content,
            exists=exists,
        )

    def _write_document(
        self,
        *,
        scope: str,
        owner_id: str,
        path: Path,
        content: str,
    ) -> MarkdownMemoryDocument:
        path.parent.mkdir(parents=True, exist_ok=True)
        normalized = content.rstrip() + "\n"
        path.write_text(normalized, encoding="utf-8")
        return MarkdownMemoryDocument(
            scope=scope,
            owner_id=owner_id,
            path=str(path),
            content=normalized,
            exists=True,
        )

    def _agent_path(self, agent_id: str) -> Path:
        if _safe_id(agent_id) == "default":
            return self.root / "agents" / "agent_memory.md"
        return self.root / "agents" / _safe_id(agent_id) / "agent_memory.md"

    def _user_path(self, user_id: str) -> Path:
        if _safe_id(user_id) == "default":
            return self.root / "users" / "user_memory.md"
        return self.root / "users" / _safe_id(user_id) / "user_memory.md"

    def _global_path(self) -> Path:
        """全局记忆固定在租户记忆根下，一份文件，不按 workspace 分目录。

        ``self.root`` 默认来自 ``memory_root()``，而它由 ``tenant_root()`` 决定。
        """

        return self.root / "global" / "global_memory.md"

    def _habits_path(self, user_id: str) -> Path:
        if _safe_id(user_id) == "default":
            return self.root / "users" / "habits.md"
        return self.root / "users" / _safe_id(user_id) / "habits.md"

    def _professional_path(self, agent_id: str) -> Path:
        return self.root / "agents" / _safe_id(agent_id) / "professional.md"


def _safe_id(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_.-]+", "_", value.strip())
    return normalized[:120] or "default"
