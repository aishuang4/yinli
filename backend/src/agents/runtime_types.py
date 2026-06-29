"""Agent 运行时类型定义。"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from src.security.permissions import UnifiedPermissionProfile, build_permission_profile


WORKSPACE_DIR = str(Path(__file__).resolve().parents[3])
FallbackEventCallback = Callable[[dict[str, Any]], None]


@dataclass(frozen=True)
class AgentModelOptions:
    """Runtime model parameters selected from the persisted Agent config."""

    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    max_react_iters: int = 40


@dataclass(frozen=True)
class AgentGuardrails:
    """Runtime guardrails used while constructing the Agent."""

    filesystem_scope: str = WORKSPACE_DIR
    shell_blacklist: list[str] = field(default_factory=list)
    network_enabled: bool = True
    human_approval: bool = False
    tool_permission_mode: str = "accept_edits"
    tool_permission_profile: str = "confirm_destructive"
    execution_backend: str = "local"
    docker_image: str = "python:3.11-slim"
    persistent_sandbox: bool = False
    timeout_seconds: int = 300
    permission_profile: UnifiedPermissionProfile = field(default_factory=UnifiedPermissionProfile)

    def __post_init__(self) -> None:
        """兼容旧 guardrails 字段，自动补齐统一权限配置。"""

        filesystem_scope = self.filesystem_scope or WORKSPACE_DIR
        expanded_scope = os.path.abspath(os.path.expanduser(filesystem_scope))
        if expanded_scope == os.path.abspath(os.sep):
            expanded_scope = WORKSPACE_DIR
        object.__setattr__(self, "filesystem_scope", expanded_scope)

        profile = self.permission_profile
        if profile == UnifiedPermissionProfile():
            profile = build_permission_profile(self)
        object.__setattr__(self, "permission_profile", profile)


@dataclass(frozen=True)
class McpRuntimeSpec:
    """Serializable MCP server selection passed from route code to factory."""

    name: str
    transport: str
    url: str = ""
    command: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    enable_tools: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class KnowledgeToolRuntimeSpec:
    """Serializable knowledge base tool selection passed from route code to factory."""

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

    def cache_key(self) -> str:
        return (
            f"mcp-{int(self.allow_mcp)}:"
            f"knowledge-{int(self.allow_knowledge)}:"
            f"bash-{int(self.allow_bash)}:"
            f"http-{int(self.allow_http)}"
        )
