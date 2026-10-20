"""HTTP 工具定义 YAML 编解码与发现。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from src.catalog.paths import ID_PATTERN, atomic_write_text
from src.catalog.secrets import (
    load_http_auth,
    redact_headers,
    split_headers,
    store_http_auth,
    yaml_has_plaintext_secrets,
)
from src.catalog.system_defaults import utc_now


@dataclass
class HttpToolFileRecord:
    id: str
    name: str
    description: str = ""
    category: str = "custom"
    method: str = "POST"
    url: str = ""
    auth_type: str = "none"
    auth_ref: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    header_refs: dict[str, str] = field(default_factory=dict)
    query_template: dict[str, Any] = field(default_factory=dict)
    body_template: dict[str, Any] = field(default_factory=dict)
    parameter_schema: dict[str, Any] = field(default_factory=dict)
    parameter_instructions: str = ""
    response_mode: str = "full"
    response_path: str = ""
    readonly: bool = True
    approval_policy: str = "inherit"
    enabled: bool = True
    workspace_id: str = "default"
    source_scope: str = "workspace"
    source_path: str = ""
    created_at: str = ""
    updated_at: str = ""


def dump_http_yaml(record: HttpToolFileRecord) -> str:
    payload = {
        "id": record.id,
        "name": record.name,
        "description": record.description,
        "category": record.category,
        "method": record.method,
        "url": record.url,
        "auth_type": record.auth_type,
        "auth_ref": record.auth_ref,
        "headers": redact_headers(record.headers),
        "header_refs": dict(record.header_refs),
        "query_template": dict(record.query_template),
        "body_template": dict(record.body_template),
        "parameter_schema": dict(record.parameter_schema),
        "parameter_instructions": record.parameter_instructions,
        "response_mode": record.response_mode,
        "response_path": record.response_path,
        "readonly": bool(record.readonly),
        "approval_policy": record.approval_policy,
        "enabled": bool(record.enabled),
        "created_at": record.created_at,
        "updated_at": record.updated_at or utc_now(),
    }
    if yaml_has_plaintext_secrets(payload):
        raise ValueError("HTTP 工具 YAML 不得包含明文密钥")
    return yaml.safe_dump(payload, allow_unicode=True, default_flow_style=False, sort_keys=False)


def parse_http_yaml(text: str, *, path: Path, workspace_id: str, source_scope: str) -> HttpToolFileRecord:
    payload = yaml.safe_load(text) or {}
    if not isinstance(payload, dict):
        raise ValueError("HTTP 工具 YAML 必须是对象")
    if yaml_has_plaintext_secrets(payload):
        raise ValueError("HTTP 工具 YAML 不得包含明文密钥")
    tool_id = str(payload.get("id") or path.stem).strip()
    if not ID_PATTERN.match(tool_id) or path.stem != tool_id:
        raise ValueError(f"HTTP 工具 id 与文件名不一致: {path.name}")
    headers = payload.get("headers") or {}
    refs = payload.get("header_refs") or {}
    if not isinstance(headers, dict) or not isinstance(refs, dict):
        raise ValueError("headers / header_refs 必须是对象")
    return HttpToolFileRecord(
        id=tool_id,
        name=str(payload.get("name") or tool_id),
        description=str(payload.get("description") or ""),
        category=str(payload.get("category") or "custom"),
        method=str(payload.get("method") or "POST").upper(),
        url=str(payload.get("url") or ""),
        auth_type=str(payload.get("auth_type") or payload.get("authType") or "none"),
        auth_ref=str(payload.get("auth_ref") or payload.get("authRef") or ""),
        headers={str(key): str(value) for key, value in headers.items()},
        header_refs={str(key): str(value) for key, value in refs.items()},
        query_template=dict(payload.get("query_template") or payload.get("queryTemplate") or {}),
        body_template=dict(payload.get("body_template") or payload.get("bodyTemplate") or {}),
        parameter_schema=dict(payload.get("parameter_schema") or payload.get("parameterSchema") or {}),
        parameter_instructions=str(payload.get("parameter_instructions") or payload.get("parameterInstructions") or ""),
        response_mode=str(payload.get("response_mode") or payload.get("responseMode") or "full"),
        response_path=str(payload.get("response_path") or payload.get("responsePath") or ""),
        readonly=bool(payload.get("readonly", True)),
        approval_policy=str(payload.get("approval_policy") or payload.get("approvalPolicy") or "inherit"),
        enabled=bool(payload.get("enabled", True)),
        workspace_id=workspace_id,
        source_scope=source_scope,
        source_path=str(path),
        created_at=str(payload.get("created_at") or payload.get("createdAt") or ""),
        updated_at=str(payload.get("updated_at") or payload.get("updatedAt") or ""),
    )


def write_http_file(path: Path, record: HttpToolFileRecord, *, auth_config: dict[str, Any] | None = None) -> HttpToolFileRecord:
    public, refs = split_headers(record.headers, server_id=record.id)
    record.headers = public
    record.header_refs = {**dict(record.header_refs), **refs}
    if auth_config is not None:
        record.auth_ref = store_http_auth(record.id, auth_config)
    record.updated_at = utc_now()
    if not record.created_at:
        record.created_at = record.updated_at
    atomic_write_text(path, dump_http_yaml(record))
    return record


def runtime_auth_config(record: HttpToolFileRecord) -> dict[str, Any]:
    return load_http_auth(record.auth_ref)


def _scan_dir(directory: Path, *, workspace_id: str, source_scope: str) -> list[HttpToolFileRecord]:
    if not directory.is_dir():
        return []
    records: list[HttpToolFileRecord] = []
    for path in sorted(directory.glob("*.yaml")):
        if path.name.startswith("."):
            continue
        try:
            records.append(parse_http_yaml(
                path.read_text(encoding="utf-8"),
                path=path,
                workspace_id=workspace_id,
                source_scope=source_scope,
            ))
        except (OSError, ValueError, yaml.YAMLError):
            continue
    return records


def discover_http_files(*, project_root: Path | None = None) -> list[HttpToolFileRecord]:
    """只讀 `<tenant-root>/tools`。外部工作目錄中的 `.agents/tools` 不進入目錄。"""

    del project_root
    from src.config.runtime_paths import global_tools_root

    return _scan_dir(global_tools_root(), workspace_id="global", source_scope="global")


def project_http_ids(project_root: Path) -> set[str]:
    """外部目錄不再提供 HTTP Tool 身份。"""

    del project_root
    return set()
