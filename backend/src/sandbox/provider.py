"""沙箱提供者。测试注入 FakeSandbox，托管运行使用记录后的同一接口。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SandboxSpec:
    name: str
    binds: tuple[str, ...]
    labels: dict[str, str]
    host_config: dict[str, Any]


class SandboxProvider:
    def create(self, spec: SandboxSpec) -> None:
        raise NotImplementedError

    def exec(self, name: str, command: list[str]) -> str:
        raise NotImplementedError

    def delete(self, name: str) -> None:
        raise NotImplementedError

    def delete_by_label(self, key: str, value: str) -> None:
        raise NotImplementedError


_provider: SandboxProvider | None = None


def get_sandbox_provider() -> SandboxProvider:
    global _provider
    if _provider is None:
        _provider = DockerSandbox()
    return _provider


class DockerSandbox(SandboxProvider):
    """真实 daemon。创建由 HostedDockerWorkspace 在启动容器时完成。"""

    def create(self, spec: SandboxSpec) -> None:
        return None

    def exec(self, name: str, command: list[str]) -> str:
        raise RuntimeError("宿主不直接执行脚本，请通过沙箱容器")

    def delete(self, name: str) -> None:
        return None

    def delete_by_label(self, key: str, value: str) -> None:
        import subprocess

        listed = subprocess.run(
            ["docker", "ps", "-aq", "--filter", f"label={key}={value}"],
            capture_output=True,
            text=True,
            check=False,
        )
        ids = [item for item in listed.stdout.split() if item]
        if ids:
            subprocess.run(["docker", "rm", "-f", *ids], capture_output=True, check=False)


def set_sandbox_provider(provider: SandboxProvider | None) -> None:
    global _provider
    _provider = provider
