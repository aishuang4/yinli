"""习惯和专业区的 Markdown 文件。

旧的 `user_memory.md`、`agent_memory.md`、`global_memory.md` 在 `legacy.markdown_files`。
读取不存在的文件时不落盘；首次保存时才创建。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from src.config.runtime_paths import memory_root


@dataclass(frozen=True)
class MarkdownMemoryDocument:
    """Markdown 记忆读取结果。"""

    scope: str
    owner_id: str
    path: str
    content: str
    exists: bool


class MarkdownMemoryStore:
    """习惯和专业区文件。"""

    def __init__(self, root: Path | None = None):
        self.root = root or memory_root()

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

    def _habits_path(self, user_id: str) -> Path:
        if _safe_id(user_id) == "default":
            return self.root / "users" / "habits.md"
        return self.root / "users" / _safe_id(user_id) / "habits.md"

    def _professional_path(self, agent_id: str) -> Path:
        return self.root / "agents" / _safe_id(agent_id) / "professional.md"


def _safe_id(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_.-]+", "_", value.strip())
    return normalized[:120] or "default"
