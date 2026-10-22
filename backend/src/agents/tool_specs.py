"""这一轮要挂上的 MCP、知识库工具，以及主 Agent 可见的工具开关。"""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class McpRuntimeSpec:
    """路由交给工厂的 MCP 服务选择。"""

    name: str
    transport: str
    url: str = ""
    command: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    enable_tools: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class KnowledgeToolRuntimeSpec:
    """路由交给工厂的知识库或 HTTP 工具选择。"""

    id: str
    name: str
    kind: str = "legacy_knowledge"
    description: str = ""
    url: str = ""
    authorization: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    default_payload: dict[str, object] = field(default_factory=dict)
    parameter_instructions: str = ""
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RuntimeToolScope:
    """本轮主 Agent 可见的工具范围。"""

    allow_mcp: bool = True
    allow_knowledge: bool = True
    allow_bash: bool = True
    allow_http: bool = True
    allow_web: bool = True

    def cache_key(self) -> str:
        return (
            f"mcp-{int(self.allow_mcp)}:"
            f"knowledge-{int(self.allow_knowledge)}:"
            f"bash-{int(self.allow_bash)}:"
            f"http-{int(self.allow_http)}:"
            f"web-{int(self.allow_web)}"
        )
