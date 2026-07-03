"""Markdown 长期记忆文件存储。

记忆文件采用懒创建：读取不存在的记忆时返回默认模板但不落盘；首次保存时才创建文件。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from src.storage.database import BACKEND_ROOT

USER_MEMORY_TEMPLATE = """# Summary

<!-- 用户单独记忆摘要。 -->

# Stable Preferences

<!-- 用户长期偏好、协作习惯和输出风格。 -->

# Profile

<!-- 用户稳定信息、职责、常用技术栈和常用项目。 -->

# Corrections

<!-- 用户纠正过的事实、规则和表达偏好。 -->

# Negative Rules

<!-- 明确禁止或应避免的做法，优先级高于普通偏好。 -->

# Source References

<!-- 来源 session/run/message 引用，只保留短引用，不复制长对话。 -->
"""


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


GLOBAL_MEMORY_TEMPLATE = """# Summary

<!-- 全局偏好记忆摘要。 -->

# Stable Preferences

<!-- 整个工作台或 Workspace 的默认偏好、语言和文档风格。 -->

# Workspace Rules

<!-- 对多个 Agent、Team 或项目都有效的流程、安全和架构约定。 -->

# Corrections

<!-- 全局纠正规则，例如已废弃能力和后续禁止使用的旧链路。 -->

# Negative Rules

<!-- 明确禁止或应避免的全局做法。 -->

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

    def read_global_memory(self, workspace_id: str = "default") -> MarkdownMemoryDocument:
        """读取全局偏好记忆；不存在时返回默认模板但不创建文件。"""

        return self._read_document(
            scope="global_preference",
            owner_id=workspace_id,
            path=self._global_path(workspace_id),
            template=GLOBAL_MEMORY_TEMPLATE,
        )

    def write_global_memory(self, workspace_id: str, content: str) -> MarkdownMemoryDocument:
        """保存全局偏好记忆；首次写入时创建文件。"""

        return self._write_document(
            scope="global_preference",
            owner_id=workspace_id,
            path=self._global_path(workspace_id),
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
        return self.root / "agents" / f"{_safe_id(agent_id)}.md"

    def _user_path(self, user_id: str) -> Path:
        return self.root / "users" / f"{_safe_id(user_id)}.md"

    def _global_path(self, workspace_id: str) -> Path:
        return self.root / "global" / f"{_safe_id(workspace_id)}.md"


def _safe_id(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_.-]+", "_", value.strip())
    return normalized[:120] or "default"
