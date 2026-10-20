"""Agent 运行时类型定义。"""

import os
from dataclasses import dataclass, field
from typing import Any, Callable

from src.security.permissions import UnifiedPermissionProfile, build_permission_profile


MAX_REACT_ITERS = 512
FallbackEventCallback = Callable[[dict[str, Any]], None]

# Skill 加载模式。交互式 Chat 由模型按需调用内置 `Skill` viewer；
# scheduled firing 只使用确认时冻结、首轮前预加载的 required Skill，Toolkit 不注册 viewer。
# 模式决定模型可见工具集，必须进入 Agent / runtime 缓存键。
SKILL_LOADING_INTERACTIVE = "interactive_on_demand"
SKILL_LOADING_SCHEDULED = "scheduled_preloaded"
SKILL_LOADING_MODES = frozenset({SKILL_LOADING_INTERACTIVE, SKILL_LOADING_SCHEDULED})


def normalize_skill_loading_mode(value: str | None) -> str:
    """空值按交互式处理；未知模式直接报错，避免静默退回会暴露 viewer 的工具集。"""

    mode = str(value or "").strip() or SKILL_LOADING_INTERACTIVE
    if mode not in SKILL_LOADING_MODES:
        raise ValueError(f"未知 Skill 加载模式: {mode}")
    return mode


@dataclass(frozen=True)
class AgentModelOptions:
    """Runtime model parameters selected from the persisted Agent config."""

    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    max_react_iters: int = 128


@dataclass(frozen=True)
class AgentGuardrails:
    """Runtime guardrails used while constructing the Agent."""

    filesystem_scope: str = ""
    shell_blacklist: list[str] = field(default_factory=list)
    network_enabled: bool = True
    operation_scope: str = "normal"
    human_approval: bool = False
    tool_permission_mode: str = "accept_edits"
    tool_permission_profile: str = "confirm_destructive"
    execution_backend: str = "local"
    docker_image: str = "python:3.11-slim"
    persistent_sandbox: bool = False
    timeout_seconds: int = 300
    permission_profile: UnifiedPermissionProfile = field(default_factory=UnifiedPermissionProfile)

    def __post_init__(self) -> None:
        """空範圍與根目錄保持未授權，不回退到 default project root。"""

        filesystem_scope = str(self.filesystem_scope or "").strip()
        if filesystem_scope in {"/", os.path.abspath(os.sep)}:
            filesystem_scope = ""
        elif filesystem_scope:
            filesystem_scope = os.path.abspath(os.path.expanduser(filesystem_scope))
            if filesystem_scope == os.path.abspath(os.sep):
                filesystem_scope = ""
        object.__setattr__(self, "filesystem_scope", filesystem_scope)

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
    id: str = ""


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
    allow_web: bool = True

    def cache_key(self) -> str:
        return (
            f"mcp-{int(self.allow_mcp)}:"
            f"knowledge-{int(self.allow_knowledge)}:"
            f"bash-{int(self.allow_bash)}:"
            f"http-{int(self.allow_http)}:"
            f"web-{int(self.allow_web)}"
        )
