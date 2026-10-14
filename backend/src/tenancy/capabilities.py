"""当前进程对前端暴露的能力。托管与桌面不同，后端入口仍要各自校验。"""

from __future__ import annotations

import shutil
from dataclasses import dataclass

from src.config.runtime_paths import default_project_root
from src.config.settings import settings
from src.tenancy.context import is_hosted_mode


@dataclass(frozen=True)
class Capabilities:
    local_project_paths: bool
    stdio_mcp: bool
    local_execution: bool
    sandbox_execution: bool
    sandbox_images: tuple[str, ...]
    cloud_login: bool
    custom_models: bool

    def as_public(self) -> dict[str, object]:
        return {
            "localProjectPaths": self.local_project_paths,
            "stdioMcp": self.stdio_mcp,
            "localExecution": self.local_execution,
            "sandboxExecution": self.sandbox_execution,
            "sandboxImages": list(self.sandbox_images),
            "cloudLogin": self.cloud_login,
            "customModels": self.custom_models,
        }


def sandbox_images() -> tuple[str, ...]:
    items = tuple(part.strip() for part in settings.sandbox_images.split(",") if part.strip())
    return items or ("python:3.11-slim",)


def capabilities() -> Capabilities:
    if is_hosted_mode():
        return Capabilities(
            local_project_paths=False,
            stdio_mcp=False,
            local_execution=False,
            sandbox_execution=True,
            sandbox_images=sandbox_images(),
            cloud_login=False,
            custom_models=True,
        )
    docker_ready = bool(settings.sandbox_docker_host.strip() or shutil.which("docker"))
    return Capabilities(
        local_project_paths=True,
        stdio_mcp=True,
        local_execution=True,
        sandbox_execution=docker_ready,
        sandbox_images=(),
        cloud_login=True,
        custom_models=True,
    )


def hosted_docker_image(requested: str) -> str:
    images = sandbox_images()
    if requested in images:
        return requested
    return images[0]


def coerce_hosted_guardrails(guardrails):
    """托管下命令只能走沙箱，文件范围收进当前 Project。"""

    if guardrails is None or not is_hosted_mode():
        return guardrails
    image = hosted_docker_image(str(getattr(guardrails, "docker_image", "") or ""))
    scope = str(default_project_root())
    if hasattr(guardrails, "model_copy"):
        return guardrails.model_copy(update={
            "execution_backend": "docker",
            "persistent_sandbox": True,
            "docker_image": image,
            "filesystem_scope": scope,
        })
    from src.agents.runtime_types import AgentGuardrails

    return AgentGuardrails(
        filesystem_scope=scope,
        shell_blacklist=list(getattr(guardrails, "shell_blacklist", []) or []),
        network_enabled=bool(getattr(guardrails, "network_enabled", True)),
        human_approval=bool(getattr(guardrails, "human_approval", False)),
        tool_permission_mode=str(getattr(guardrails, "tool_permission_mode", "accept_edits")),
        tool_permission_profile=str(getattr(guardrails, "tool_permission_profile", "confirm_destructive")),
        execution_backend="docker",
        docker_image=image,
        persistent_sandbox=True,
        timeout_seconds=int(getattr(guardrails, "timeout_seconds", 300) or 300),
        permission_profile=getattr(guardrails, "permission_profile"),
    )
