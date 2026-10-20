"""MCP 运行时规格纯函数。

servers 由调用方传入，禁止 import `src.server.routes`。
stdio 无论是否带 url 都不可无人值守注入。
"""

from __future__ import annotations

import re
from typing import Any


def _attr(server: Any, key: str, default: Any = None) -> Any:
    if isinstance(server, dict):
        return server.get(key, server.get(_camel(key), default))
    value = getattr(server, key, None)
    if value is not None:
        return value
    return getattr(server, _camel(key), default)


def _camel(key: str) -> str:
    parts = key.split("_")
    return parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:])


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return slug or "custom-mcp"


def mcp_injectable(transport: str, url: str) -> bool:
    """库存可注入谓词：stdio 一律否；HTTP MCP 需要非空 url。"""

    normalized = str(transport or "").strip().lower()
    if normalized == "stdio":
        return False
    return bool(str(url or "").strip()) and (
        normalized in {"sse", "websocket"} or bool(str(url or "").strip())
    )


def resolve_mcp_runtime_servers(
    tool_ids: list[str],
    servers: list[Any],
    *,
    allowed_server_ids: set[str] | frozenset[str] | None = None,
    builtin_server_ids: set[str] | frozenset[str] | tuple[str, ...] = (),
) -> list[dict[str, object]]:
    """把选中的 MCP tool id 解析成 Agent 运行时 server spec。"""

    selected = {str(item) for item in tool_ids if str(item).strip()}
    if not selected:
        return []
    builtin = {str(item) for item in builtin_server_ids}
    runtime_servers: list[dict[str, object]] = []
    for server in servers:
        server_id = str(_attr(server, "id") or "")
        if not server_id:
            continue
        if (
            allowed_server_ids is not None
            and server_id not in allowed_server_ids
            and server_id not in builtin
        ):
            continue
        status = str(_attr(server, "status") or "")
        enabled = bool(_attr(server, "enabled", False))
        if status != "installed" or not enabled:
            continue
        tools = list(_attr(server, "tools") or [])
        enabled_tool_names: list[str] = []
        for tool in tools:
            tool_id = str(_attr(tool, "id") or "")
            if tool_id in selected:
                enabled_tool_names.append(str(_attr(tool, "name") or tool_id))
        if not enabled_tool_names:
            continue
        runtime_servers.append({
            "name": _slugify(server_id).replace("-", "_"),
            "transport": str(_attr(server, "transport") or ""),
            "url": str(_attr(server, "url") or ""),
            "command": str(_attr(server, "command") or ""),
            "headers": dict(_attr(server, "headers") or {}),
            "enable_tools": enabled_tool_names,
        })
    return runtime_servers
