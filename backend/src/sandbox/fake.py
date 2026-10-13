"""不连接 Docker 的沙箱替身，记下创建参数和按标签删除。"""

from __future__ import annotations

from src.sandbox.provider import SandboxProvider, SandboxSpec


class FakeSandbox(SandboxProvider):
    def __init__(self) -> None:
        self.created: list[SandboxSpec] = []
        self.deleted_labels: list[tuple[str, str]] = []
        self.exec_log: list[tuple[str, list[str]]] = []
        self.output = "sandbox-ok"

    def create(self, spec: SandboxSpec) -> None:
        self.created.append(spec)

    def exec(self, name: str, command: list[str]) -> str:
        self.exec_log.append((name, command))
        return self.output

    def delete(self, name: str) -> None:
        self.created = [item for item in self.created if item.name != name]

    def delete_by_label(self, key: str, value: str) -> None:
        self.deleted_labels.append((key, value))
        self.created = [
            item for item in self.created if item.labels.get(key) != value
        ]
