"""Subagent 定义模型。

该模块只描述 Markdown + YAML frontmatter 解析后的稳定结构。
Subagent 定义不进入 SQLite，SQLite 只记录运行 invocation。
"""

from dataclasses import dataclass, field
from typing import Any


SUBAGENT_PROJECT_DIR = ".agents/subagents"
SUBAGENT_USER_DIR = "~/.agent-workstation/subagents"


@dataclass(frozen=True)
class SubagentDefinition:
    """单个 Subagent 文件定义。"""

    name: str
    description: str
    prompt: str
    scope: str
    path: str
    tools: list[str] | None = None
    disallowed_tools: list[str] = field(default_factory=list)
    model: str = "inherit"
    permission_mode: str = "default"
    max_turns: int = 20
    skills: list[str] = field(default_factory=list)
    mcp_servers: list[Any] = field(default_factory=list)
    color: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    content_hash: str = ""


@dataclass(frozen=True)
class SubagentLoadError:
    """Subagent 定义文件解析错误。"""

    scope: str
    path: str
    message: str


@dataclass(frozen=True)
class SubagentCatalog:
    """合并不同 scope 后的 Subagent catalog。"""

    definitions: dict[str, SubagentDefinition]
    errors: list[SubagentLoadError] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    fingerprint: str = "empty"
