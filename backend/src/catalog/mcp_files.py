"""MCP 定义 YAML 编解码与发现。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from src.catalog.paths import (
    ID_PATTERN,
    atomic_write_text,
    mcp_dir,
    project_mcp_dir,
    workspaces_root,
)
from src.catalog.secrets import redact_headers, split_headers, yaml_has_plaintext_secrets
from src.catalog.system_defaults import utc_now


@dataclass
class McpFileRecord:
    id: str
    name: str
    description: str = ""
    category: str = "自定义"
    transport: str = "sse"
    command: str = ""
    url: str = ""
    homepage: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    header_refs: dict[str, str] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    tools: list[dict[str, Any]] = field(default_factory=list)
    status: str = "installed"
    enabled: bool = True
    workspace_id: str = "default"
    source_scope: str = "workspace"
    source_path: str = ""
    updated_at: str = ""


def dump_mcp_yaml(record: McpFileRecord) -> str:
    payload = {
        "id": record.id,
        "name": record.name,
        "description": record.description,
        "category": record.category,
        "transport": record.transport,
        "command": record.command,
        "url": record.url,
        "homepage": record.homepage,
        "headers": redact_headers(record.headers),
        "header_refs": dict(record.header_refs),
        "tags": list(record.tags),
        "tools": list(record.tools),
        "status": record.status,
        "enabled": bool(record.enabled),
        "updated_at": record.updated_at or utc_now(),
    }
    if yaml_has_plaintext_secrets(payload):
        raise ValueError("MCP YAML 不得包含明文密钥")
    return yaml.safe_dump(payload, allow_unicode=True, default_flow_style=False, sort_keys=False)


def parse_mcp_yaml(text: str, *, path: Path, workspace_id: str, source_scope: str) -> McpFileRecord:
    payload = yaml.safe_load(text) or {}
    if not isinstance(payload, dict):
        raise ValueError("MCP YAML 必须是对象")
    if yaml_has_plaintext_secrets(payload):
        raise ValueError("MCP YAML 不得包含明文密钥")
    server_id = str(payload.get("id") or path.stem).strip()
    if not ID_PATTERN.match(server_id) or path.stem != server_id:
        raise ValueError(f"MCP id 与文件名不一致: {path.name}")
    headers = payload.get("headers") or {}
    refs = payload.get("header_refs") or payload.get("headerRefs") or {}
    if not isinstance(headers, dict) or not isinstance(refs, dict):
        raise ValueError("headers / header_refs 必须是对象")
    return McpFileRecord(
        id=server_id,
        name=str(payload.get("name") or server_id),
        description=str(payload.get("description") or ""),
        category=str(payload.get("category") or "自定义"),
        transport=str(payload.get("transport") or "sse"),
        command=str(payload.get("command") or ""),
        url=str(payload.get("url") or ""),
        homepage=str(payload.get("homepage") or ""),
        headers={str(key): str(value) for key, value in headers.items()},
        header_refs={str(key): str(value) for key, value in refs.items()},
        tags=[str(item) for item in list(payload.get("tags") or [])],
        tools=[item for item in list(payload.get("tools") or []) if isinstance(item, dict)],
        status=str(payload.get("status") or "installed"),
        enabled=bool(payload.get("enabled", True)),
        workspace_id=workspace_id,
        source_scope=source_scope,
        source_path=str(path),
        updated_at=str(payload.get("updated_at") or ""),
    )


def write_mcp_file(path: Path, record: McpFileRecord) -> None:
    public, refs = split_headers(record.headers, server_id=record.id)
    record.headers = public
    record.header_refs = {**dict(record.header_refs), **refs}
    record.updated_at = utc_now()
    atomic_write_text(path, dump_mcp_yaml(record))


def _scan_dir(directory: Path, *, workspace_id: str, source_scope: str) -> list[McpFileRecord]:
    if not directory.is_dir():
        return []
    records: list[McpFileRecord] = []
    for path in sorted(directory.glob("*.yaml")):
        if path.name.startswith("."):
            continue
        try:
            records.append(parse_mcp_yaml(
                path.read_text(encoding="utf-8"),
                path=path,
                workspace_id=workspace_id,
                source_scope=source_scope,
            ))
        except (OSError, ValueError, yaml.YAMLError):
            continue
    return records


def discover_mcp_files(*, project_root: Path | None = None) -> list[McpFileRecord]:
    records: list[McpFileRecord] = []
    root = workspaces_root()
    if root.is_dir():
        for child in sorted(root.iterdir()):
            if not child.is_dir() or child.name.startswith("_"):
                continue
            records.extend(_scan_dir(mcp_dir(child.name), workspace_id=child.name, source_scope="workspace"))
    if project_root is not None:
        records.extend(_scan_dir(
            project_mcp_dir(project_root),
            workspace_id="default",
            source_scope="project",
        ))
    return records


def project_mcp_ids(project_root: Path) -> set[str]:
    return {item.id for item in _scan_dir(project_mcp_dir(project_root), workspace_id="default", source_scope="project")}
