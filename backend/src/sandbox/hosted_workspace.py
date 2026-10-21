"""托管容器规格。名字带上租户和 Project，避免默认 Agent 共用一个容器。"""

from __future__ import annotations

import hashlib
from pathlib import Path

from agentscope.workspace import DockerWorkspace

from src.config import runtime_paths
from src.config.settings import settings
from src.sandbox.provider import SandboxSpec, get_sandbox_provider
from src.tenancy.capabilities import hosted_docker_image


def container_name(tenant_id: str, project_slug: str, agent_id: str, extra: str = "") -> str:
    material = f"{tenant_id}:{project_slug}:{agent_id}:{extra}"
    return "y" + hashlib.sha1(material.encode("utf-8")).hexdigest()[:24]


def to_daemon_path(path: Path) -> str:
    resolved = path.resolve()
    root = runtime_paths.tenant_root().resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("沙箱只能挂载当前租户目录")
    host_root = Path(settings.sandbox_host_data_root or str(runtime_paths.USER_DATA_ROOT)).resolve()
    data_root = Path(runtime_paths.USER_DATA_ROOT).resolve()
    try:
        relative = resolved.relative_to(data_root)
    except ValueError:
        relative = resolved.relative_to(root)
        relative = Path("tenants") / root.name / relative
    return str(host_root / relative)


def primary_mount(project_slug: str, fallback: Path | None = None) -> Path:
    """托管只挂一个目录：该 Project 的 primaryPath，缺省时用 projects/<slug>/。"""

    from src.storage.database import get_project

    project = get_project(project_slug) if project_slug else None
    primary = str((project or {}).get("primaryPath") or "").strip()
    if primary:
        return Path(primary).expanduser()
    if fallback is not None:
        return fallback
    return runtime_paths.project_default_root(project_slug or "default")


def build_hosted_spec(
    *,
    tenant_id: str,
    project_slug: str,
    agent_id: str,
    fallback_root: Path | None = None,
    network_enabled: bool = True,
    extra: str = "",
) -> SandboxSpec:
    name = container_name(tenant_id, project_slug, agent_id, extra)
    mount_root = primary_mount(project_slug, fallback_root)
    mounts = [f"{to_daemon_path(mount_root)}:/workspace:rw"]
    network = settings.sandbox_network if network_enabled else "none"
    if not network_enabled:
        network = "none"
    host_config = {
        "Binds": mounts,
        "Memory": int(settings.sandbox_memory_mb) * 1024 * 1024,
        "NanoCpus": int(float(settings.sandbox_cpus) * 1_000_000_000),
        "PidsLimit": int(settings.sandbox_pids),
        "NetworkMode": network,
        "Runtime": settings.sandbox_runtime,
        "CapDrop": ["ALL"],
        "SecurityOpt": ["no-new-privileges"],
        "Privileged": False,
    }
    labels = {
        "agentscope.workspace": "true",
        "agentscope.workspace.id": name,
        "yinli.tenant": tenant_id,
        "yinli.project": project_slug,
    }
    return SandboxSpec(name=name, binds=tuple(mounts), labels=labels, host_config=host_config)


class HostedDockerWorkspace(DockerWorkspace):
    """托管沙箱。真正启动容器时走沙箱提供者，测试里是 FakeSandbox。"""

    def __init__(self, spec: SandboxSpec, *, project_root: Path, base_image: str) -> None:
        super().__init__(
            base_image=base_image,
            host_workdir=str(project_root),
        )
        self.spec = spec

    async def _create_and_start_container(self) -> None:
        provider = get_sandbox_provider()
        if provider.__class__.__name__ == "FakeSandbox":
            return
        await super()._create_and_start_container()


def open_hosted_workspace(
    *,
    tenant_id: str,
    project_slug: str,
    agent_id: str,
    base_image: str,
    fallback_root: Path | None = None,
    network_enabled: bool = True,
    extra: str = "",
) -> HostedDockerWorkspace:
    mount_root = primary_mount(project_slug, fallback_root)
    spec = build_hosted_spec(
        tenant_id=tenant_id,
        project_slug=project_slug,
        agent_id=agent_id,
        fallback_root=fallback_root,
        network_enabled=network_enabled,
        extra=extra,
    )
    spec = SandboxSpec(
        name=spec.name,
        binds=spec.binds,
        labels=spec.labels,
        host_config={**spec.host_config, "Binds": list(spec.binds)},
    )
    # 镜像必须在白名单内。
    image = hosted_docker_image(base_image)
    get_sandbox_provider().create(spec)
    workspace = HostedDockerWorkspace(spec, project_root=mount_root, base_image=image)
    if not isinstance(workspace, HostedDockerWorkspace):
        raise RuntimeError("托管命令只能在沙箱中执行")
    return workspace
