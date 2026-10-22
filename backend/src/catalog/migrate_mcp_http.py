"""MCP / HTTP 文件 catalog 的启动钩子。

旧库封存，不在启动时把表记录导出成 YAML。YAML 只作为 `<tenant>/mcp` 与 `<tenant>/tools` 的导入导出目录。
"""

from __future__ import annotations

CATALOG_NAMESPACE = "catalog"
MCP_MIGRATION_KEY = "mcp_migration_v1"
HTTP_MIGRATION_KEY = "http_tool_migration_v1"


def _marker_done(key: str) -> bool:
    from src.storage.config.json_document_store import get_json_record

    marker = get_json_record(CATALOG_NAMESPACE, key) or {}
    return str(marker.get("status") or "") == "done"


def is_mcp_catalog_migrated() -> bool:
    return _marker_done(MCP_MIGRATION_KEY)


def is_http_catalog_migrated() -> bool:
    return _marker_done(HTTP_MIGRATION_KEY)


def ensure_mcp_file_catalog(*, builtin_ids: set[str]) -> None:
    """不在启动时把表记录写成 YAML。"""

    del builtin_ids


def ensure_http_file_catalog() -> None:
    """不在启动时把表记录写成 YAML。"""

    return None
