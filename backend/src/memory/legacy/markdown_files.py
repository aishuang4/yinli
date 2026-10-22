"""旧三层 Markdown 文件：`user_memory.md`、`agent_memory.md`、`global_memory.md`。

只给记忆管理页和旧反思使用。习惯与专业区不走这里。
"""

from __future__ import annotations

from src.memory.markdown_store import MarkdownMemoryDocument, MarkdownMemoryStore, _safe_id

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


class LegacyMarkdownStore(MarkdownMemoryStore):
    """读写旧三层记忆文件。读取不存在的文件时返回模板且不落盘。"""

    def read_agent_memory(self, agent_id: str) -> MarkdownMemoryDocument:
        return self._read_document(
            scope="agent",
            owner_id=agent_id,
            path=self._agent_path(agent_id),
            template=AGENT_MEMORY_TEMPLATE,
        )

    def write_agent_memory(self, agent_id: str, content: str) -> MarkdownMemoryDocument:
        return self._write_document(
            scope="agent",
            owner_id=agent_id,
            path=self._agent_path(agent_id),
            content=content,
        )

    def read_user_memory(self, user_id: str = "default") -> MarkdownMemoryDocument:
        return self._read_document(
            scope="user",
            owner_id=user_id,
            path=self._user_path(user_id),
            template=USER_MEMORY_TEMPLATE,
        )

    def write_user_memory(self, user_id: str, content: str) -> MarkdownMemoryDocument:
        return self._write_document(
            scope="user",
            owner_id=user_id,
            path=self._user_path(user_id),
            content=content,
        )

    def read_global_memory(self) -> MarkdownMemoryDocument:
        return self._read_document(
            scope="global_preference",
            owner_id="tenant",
            path=self._global_path(),
            template=GLOBAL_MEMORY_TEMPLATE,
        )

    def write_global_memory(self, content: str) -> MarkdownMemoryDocument:
        return self._write_document(
            scope="global_preference",
            owner_id="tenant",
            path=self._global_path(),
            content=content,
        )

    def _agent_path(self, agent_id: str):
        if _safe_id(agent_id) == "default":
            return self.root / "agents" / "agent_memory.md"
        return self.root / "agents" / _safe_id(agent_id) / "agent_memory.md"

    def _user_path(self, user_id: str):
        if _safe_id(user_id) == "default":
            return self.root / "users" / "user_memory.md"
        return self.root / "users" / _safe_id(user_id) / "user_memory.md"

    def _global_path(self):
        return self.root / "global" / "global_memory.md"
