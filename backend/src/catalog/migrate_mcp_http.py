"""把 sqlite MCP / HTTP 工具定义一次性导出为 YAML。"""

from __future__ import annotations

import logging
from typing import Any

from src.catalog.http_files import HttpToolFileRecord, write_http_file
from src.catalog.mcp_files import McpFileRecord, write_mcp_file
from src.catalog.system_defaults import utc_now

logger = logging.getLogger(__name__)
CATALOG_NAMESPACE = "catalog"
MCP_MIGRATION_KEY = "mcp_migration_v1"
HTTP_MIGRATION_KEY = "http_tool_migration_v1"


def _marker_done(key: str) -> bool:
    from src.storage.database import get_json_record

    marker = get_json_record(CATALOG_NAMESPACE, key) or {}
    return str(marker.get("status") or "") == "done"


def _write_marker(key: str, payload: dict[str, Any]) -> None:
    from src.storage.database import upsert_json_record

    upsert_json_record(CATALOG_NAMESPACE, key, payload)


def is_mcp_catalog_migrated() -> bool:
    return _marker_done(MCP_MIGRATION_KEY)


def is_http_catalog_migrated() -> bool:
    return _marker_done(HTTP_MIGRATION_KEY)


def ensure_mcp_file_catalog(*, builtin_ids: set[str]) -> None:
    """导出用户 MCP 定义；内置 server 仍由代码提供。"""

    from src.storage.database import list_mcp_servers, replace_mcp_servers

    if is_mcp_catalog_migrated():
        return
    exported = 0
    try:
        keep: dict[str, dict[str, Any]] = {}
        for row in list_mcp_servers():
            server_id = str(row.get("id") or "")
            if not server_id or server_id in builtin_ids:
                keep[server_id] = row
                continue
            workspace_id = str(row.get("workspaceId") or "default")
            record = McpFileRecord(
                id=server_id,
                name=str(row.get("name") or server_id),
                description=str(row.get("description") or ""),
                category=str(row.get("category") or "自定义"),
                transport=str(row.get("transport") or "sse"),
                command=str(row.get("command") or ""),
                url=str(row.get("url") or ""),
                homepage=str(row.get("homepage") or ""),
                headers=dict(row.get("headers") or {}),
                tags=list(row.get("tags") or []),
                tools=list(row.get("tools") or []),
                status=str(row.get("status") or "installed"),
                enabled=bool(row.get("enabled", True)),
                workspace_id=workspace_id,
            )
            from src.config.runtime_paths import global_mcp_root

            write_mcp_file(global_mcp_root() / f"{server_id}.yaml", record)
            exported += 1
        replace_mcp_servers(keep)
        _write_marker(MCP_MIGRATION_KEY, {
            "schemaVersion": 1,
            "status": "done",
            "migratedAt": utc_now(),
            "exported": exported,
        })
    except Exception:
        _write_marker(MCP_MIGRATION_KEY, {"schemaVersion": 1, "status": "failed", "migratedAt": utc_now()})
        raise


def ensure_http_file_catalog() -> None:
    """导出 HTTP 工具定义并清空 sqlite 正文。"""

    from src.storage.database import list_http_tools, delete_http_tool

    if is_http_catalog_migrated():
        return
    exported = 0
    try:
        for row in list_http_tools():
            tool_id = str(row.get("id") or "")
            if not tool_id:
                continue
            workspace_id = str(row.get("workspaceId") or "default")
            record = HttpToolFileRecord(
                id=tool_id,
                name=str(row.get("name") or tool_id),
                description=str(row.get("description") or ""),
                category=str(row.get("category") or "custom"),
                method=str(row.get("method") or "POST"),
                url=str(row.get("url") or ""),
                auth_type=str(row.get("authType") or row.get("auth_type") or "none"),
                headers=dict(row.get("headers") or {}),
                query_template=dict(row.get("queryTemplate") or {}),
                body_template=dict(row.get("bodyTemplate") or {}),
                parameter_schema=dict(row.get("parameterSchema") or {}),
                parameter_instructions=str(row.get("parameterInstructions") or ""),
                response_mode=str(row.get("responseMode") or "full"),
                response_path=str(row.get("responsePath") or ""),
                readonly=bool(row.get("readonly", True)),
                approval_policy=str(row.get("approvalPolicy") or "inherit"),
                enabled=bool(row.get("enabled", True)),
                workspace_id=workspace_id,
                created_at=str(row.get("createdAt") or ""),
                updated_at=str(row.get("updatedAt") or ""),
            )
            from src.config.runtime_paths import global_tools_root

            write_http_file(
                global_tools_root() / f"{tool_id}.yaml",
                record,
                auth_config=dict(row.get("authConfig") or {}),
            )
            delete_http_tool(tool_id)
            exported += 1
        _write_marker(HTTP_MIGRATION_KEY, {
            "schemaVersion": 1,
            "status": "done",
            "migratedAt": utc_now(),
            "exported": exported,
        })
    except Exception:
        _write_marker(HTTP_MIGRATION_KEY, {"schemaVersion": 1, "status": "failed", "migratedAt": utc_now()})
        raise
