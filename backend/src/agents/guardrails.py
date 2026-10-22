"""Agent 护栏：文件范围、执行后端、超时和权限档。"""
from __future__ import annotations


from dataclasses import dataclass, field

from src.agents.workspace_paths import normalize_filesystem_scope
from src.security.permissions import UnifiedPermissionProfile, build_permission_profile


@dataclass(frozen=True)
class AgentGuardrails:
    """构造 Agent 时使用的护栏。空的文件范围和空的权限档在这里补齐。"""

    filesystem_scope: str = ""
    shell_blacklist: list[str] = field(default_factory=list)
    network_enabled: bool = True
    operation_scope: str = "normal"
    execution_backend: str = "local"
    docker_image: str = "python:3.11-slim"
    persistent_sandbox: bool = False
    timeout_seconds: int = 300
    permission_profile: UnifiedPermissionProfile = field(default_factory=UnifiedPermissionProfile)

    def __post_init__(self) -> None:
        """文件范围交给目录模块规范化；权限档为空时按能力范围生成。"""

        object.__setattr__(self, "filesystem_scope", normalize_filesystem_scope(self.filesystem_scope))
        profile = self.permission_profile
        if profile == UnifiedPermissionProfile():
            profile = build_permission_profile(self)
        object.__setattr__(self, "permission_profile", profile)
