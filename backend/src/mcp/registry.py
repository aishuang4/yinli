"""MCP 服务器目录、远程工具拉取和运行时解析。

HTTP 路由留在 src.server.routes.mcp。
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Literal, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import anyio
import httpx
from fastapi import HTTPException
from mcp.client.session import ClientSession
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client
from pydantic import BaseModel, Field, HttpUrl

from src.catalog.mcp_files import McpFileRecord, discover_mcp_files, write_mcp_file
from src.catalog.migrate_mcp_http import ensure_mcp_file_catalog, is_mcp_catalog_migrated
from src.catalog.paths import mcp_dir
from src.catalog.secrets import delete_named_secret, resolve_headers
from src.scheduler.mcp_runtime import resolve_mcp_runtime_servers as _resolve_mcp_runtime_servers
from src.storage.database import list_mcp_servers as db_list_mcp_servers, replace_mcp_servers

McpTransport = Literal["stdio", "sse", "websocket"]
McpInstallStatus = Literal["available", "installed", "error"]


class McpTool(BaseModel):
    """MCP 工具描述"""

    id: str
    name: str
    description: str
    input_schema: dict = Field(default_factory=dict, alias="inputSchema")

    model_config = {"populate_by_name": True}


class McpServer(BaseModel):
    """MCP Server 描述"""

    id: str
    name: str
    description: str
    category: str
    transport: McpTransport
    command: str = ""
    url: str = ""
    homepage: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    tools: list[McpTool] = Field(default_factory=list)
    status: McpInstallStatus = "available"
    enabled: bool = False
    installed_at: Optional[str] = Field(default=None, alias="installedAt")
    updated_at: str = Field(alias="updatedAt")

    model_config = {"populate_by_name": True, "extra": "ignore"}


class McpInstallRequest(BaseModel):
    """安装/配置 MCP Server 请求"""

    enabled: bool = True


class McpRegisterRequest(BaseModel):
    """注册自定义 MCP Server 请求"""

    name: str = Field(..., min_length=1, max_length=80)
    url: HttpUrl
    description: str = Field(default="", max_length=300)
    headers: dict[str, str] = Field(default_factory=dict)

    model_config = {"populate_by_name": True, "extra": "ignore"}


RESERVED_MCP_HEADERS = {"content-type", "accept"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "custom-mcp"


def _unique_server_id(name: str) -> str:
    base = f"custom-{_slugify(name)}"
    if base not in _mcp_servers:
        return base

    index = 2
    while f"{base}-{index}" in _mcp_servers:
        index += 1
    return f"{base}-{index}"


def _normalize_headers(headers: dict[str, str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in headers.items():
        clean_key = key.strip()
        clean_value = value.strip()
        if not clean_key or not clean_value:
            continue
        if clean_key.lower() in RESERVED_MCP_HEADERS:
            raise HTTPException(
                status_code=422,
                detail=f"Header '{clean_key}' is managed by 引力 YINLI",
            )
        normalized[clean_key] = clean_value
    return normalized


def _tool(
    server_id: str,
    tool_id: str,
    name: str,
    description: str,
    input_schema: Optional[dict] = None,
) -> McpTool:
    return McpTool.model_validate(
        {
            "id": f"{server_id}.{tool_id}",
            "name": name,
            "description": description,
            "inputSchema": input_schema or {"type": "object", "properties": {}},
        }
    )


def _server(
    server_id: str,
    name: str,
    description: str,
    category: str,
    transport: McpTransport,
    tags: list[str],
    tools: list[McpTool],
    command: str = "",
    url: str = "",
    homepage: str = "",
) -> McpServer:
    return McpServer.model_validate(
        {
            "id": server_id,
            "name": name,
            "description": description,
            "category": category,
            "transport": transport,
            "command": command,
            "url": url,
            "homepage": homepage,
            "headers": {},
            "tags": tags,
            "tools": tools,
            "status": "available",
            "enabled": False,
            "installedAt": None,
            "updatedAt": _utc_now(),
        }
    )


_builtin_mcp_servers: dict[str, McpServer] = {
    "filesystem": _server(
        "filesystem",
        "Filesystem",
        "受控文件系统访问，提供目录浏览、文件读取和文件写入能力。",
        "本地工具",
        "stdio",
        ["files", "local", "default"],
        [
            _tool("filesystem", "read_file", "read_file", "读取指定路径的文件内容"),
            _tool("filesystem", "write_file", "write_file", "写入或创建指定路径的文件"),
            _tool("filesystem", "list_directory", "list_directory", "列出目录内容"),
        ],
        command="npx -y @modelcontextprotocol/server-filesystem",
        homepage="https://github.com/modelcontextprotocol/servers",
    ),
    "git": _server(
        "git",
        "Git",
        "读取仓库状态、提交历史和 diff，辅助代码审查与变更分析。",
        "开发工具",
        "stdio",
        ["git", "code", "review"],
        [
            _tool("git", "status", "git_status", "查看工作区状态"),
            _tool("git", "log", "git_log", "查看提交历史"),
            _tool("git", "diff", "git_diff", "查看文件变更"),
        ],
        command="uvx mcp-server-git",
        homepage="https://github.com/modelcontextprotocol/servers",
    ),
    "sqlite": _server(
        "sqlite",
        "SQLite",
        "连接本地 SQLite 数据库，执行查询并检查表结构。",
        "数据工具",
        "stdio",
        ["database", "sqlite", "data"],
        [
            _tool("sqlite", "query", "sqlite_query", "执行只读 SQL 查询"),
            _tool("sqlite", "schema", "sqlite_schema", "读取数据库表结构"),
        ],
        command="uvx mcp-server-sqlite",
        homepage="https://github.com/modelcontextprotocol/servers",
    ),
    "fetch": _server(
        "fetch",
        "Fetch",
        "获取网页或 API 内容，适合需要读取公开网络资料的 Agent。",
        "网络工具",
        "stdio",
        ["network", "http", "research"],
        [
            _tool("fetch", "fetch", "fetch", "抓取指定 URL 的内容"),
        ],
        command="uvx mcp-server-fetch",
        homepage="https://github.com/modelcontextprotocol/servers",
    ),
}

_mcp_servers: dict[str, McpServer] = dict(_builtin_mcp_servers)
_mcp_header_refs: dict[str, dict[str, str]] = {}


def _normalize_remote_tools(server_id: str, raw_tools: object) -> list[McpTool]:
    if not isinstance(raw_tools, list):
        raise ValueError("MCP tools response must contain a tools list")

    tools: list[McpTool] = []
    for index, raw_tool in enumerate(raw_tools):
        if hasattr(raw_tool, "model_dump"):
            raw_tool = raw_tool.model_dump(by_alias=True)
        if not isinstance(raw_tool, dict):
            continue

        raw_name = str(raw_tool.get("name") or raw_tool.get("id") or f"tool_{index + 1}")
        tool_slug = _slugify(raw_name).replace("-", "_")
        description = str(raw_tool.get("description") or "")
        input_schema = raw_tool.get("inputSchema") or raw_tool.get("input_schema") or {
            "type": "object",
            "properties": {},
        }

        tools.append(_tool(server_id, tool_slug, raw_name, description, input_schema))

    if not tools:
        raise ValueError("No MCP tools found")
    return tools


def _post_json(
    url: str,
    payload: dict,
    request_headers: Optional[dict[str, str]] = None,
    timeout: float = 8,
) -> dict:
    body = json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=body,
        headers={
            **(request_headers or {}),
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
    return _parse_mcp_http_response(raw)


def _get_json(
    url: str,
    request_headers: Optional[dict[str, str]] = None,
    timeout: float = 8,
) -> dict:
    request = Request(
        url,
        headers={
            **(request_headers or {}),
            "Accept": "application/json",
        },
        method="GET",
    )
    with urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw)


def _parse_mcp_http_response(raw: str) -> dict:
    content = raw.strip()
    if not content:
        return {}

    # Streamable HTTP/SSE may return lines like: data: {...}
    if content.startswith("event:") or content.startswith("data:"):
        for line in content.splitlines():
            if line.startswith("data:"):
                data = line.removeprefix("data:").strip()
                if data and data != "[DONE]":
                    return json.loads(data)
        return {}

    return json.loads(content)


def _extract_tools_from_response(response: dict) -> list[dict]:
    result = response.get("result", response)
    if isinstance(result, dict):
        tools = result.get("tools")
        if isinstance(tools, list):
            return tools

    tools = response.get("tools")
    if isinstance(tools, list):
        return tools

    if isinstance(result, list):
        return result

    raise ValueError("MCP server did not return a tools list")


async def _fetch_tools_with_mcp_client(
    url: str,
    server_id: str,
    request_headers: Optional[dict[str, str]] = None,
) -> list[McpTool]:
    headers = request_headers or {}
    timeout = 10
    read_timeout = 30

    if url.rstrip("/").endswith("/sse"):
        async with sse_client(
            url,
            headers=headers,
            timeout=timeout,
            sse_read_timeout=read_timeout,
        ) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools_result = await session.list_tools()
                return _normalize_remote_tools(server_id, tools_result.tools)

    async with httpx.AsyncClient(
        headers=headers,
        timeout=httpx.Timeout(timeout, read=read_timeout),
    ) as client:
        async with streamable_http_client(
            url,
            http_client=client,
            terminate_on_close=True,
        ) as (read_stream, write_stream, _get_session_id):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools_result = await session.list_tools()
                return _normalize_remote_tools(server_id, tools_result.tools)


def _fetch_remote_mcp_tools_json_fallback(
    url: str,
    server_id: str,
    request_headers: Optional[dict[str, str]] = None,
) -> list[McpTool]:
    try:
        # MCP Streamable HTTP / JSON-RPC flow.
        _post_json(
            url,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {
                        "name": "agent-workstation",
                        "version": "0.1.0",
                    },
                },
            },
            request_headers,
        )
        tools_response = _post_json(
            url,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {},
            },
            request_headers,
        )
        return _normalize_remote_tools(server_id, _extract_tools_from_response(tools_response))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, ValueError):
        # Compatibility fallback for simple tool registries returning {"tools": [...]}.
        response = _get_json(url, request_headers)
        return _normalize_remote_tools(server_id, _extract_tools_from_response(response))


async def _fetch_remote_mcp_tools(
    url: str,
    server_id: str,
    request_headers: Optional[dict[str, str]] = None,
) -> list[McpTool]:
    from src.server.routes import mcp as mcp_routes

    try:
        return await mcp_routes._fetch_tools_with_mcp_client(url, server_id, request_headers)
    except Exception as mcp_exc:
        try:
            return await anyio.to_thread.run_sync(
                mcp_routes._fetch_remote_mcp_tools_json_fallback,
                url,
                server_id,
                request_headers,
            )
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, ValueError) as fallback_exc:
            raise HTTPException(
                status_code=422,
                detail=f"无法连接该 MCP 地址：{fallback_exc or mcp_exc}",
            ) from fallback_exc


def _server_from_file(record: McpFileRecord) -> McpServer:
    server = McpServer.model_validate({
        "id": record.id,
        "name": record.name,
        "description": record.description,
        "category": record.category,
        "transport": record.transport,
        "command": record.command,
        "url": record.url,
        "homepage": record.homepage,
        "headers": record.headers,
        "tags": record.tags,
        "tools": record.tools,
        "status": record.status,
        "enabled": record.enabled,
        "updatedAt": record.updated_at or _utc_now(),
    })
    _mcp_header_refs[record.id] = dict(record.header_refs)
    return server


def _refresh_mcp_files() -> None:
    file_ids: set[str] = set()
    for record in discover_mcp_files():
        file_ids.add(record.id)
        if record.id in _builtin_mcp_servers:
            continue
        _mcp_servers[record.id] = _server_from_file(record)
    for server_id in list(_mcp_servers):
        if server_id in _builtin_mcp_servers or server_id in file_ids:
            continue
        # sqlite 测试夹具仍可能提供用户 server
        continue


def _load_user_mcp_servers() -> None:
    records = db_list_mcp_servers()
    for item in records:
        server = McpServer.model_validate(item)
        _mcp_servers[server.id] = server
    ensure_mcp_file_catalog(builtin_ids=set(_builtin_mcp_servers))
    _refresh_mcp_files()


def _save_user_mcp_servers() -> None:
    if is_mcp_catalog_migrated():
        for server_id, server in list(_mcp_servers.items()):
            if server_id in _builtin_mcp_servers:
                continue
            record = McpFileRecord(
                id=server.id,
                name=server.name,
                description=server.description,
                category=server.category,
                transport=server.transport,
                command=server.command,
                url=server.url,
                homepage=server.homepage,
                headers=dict(server.headers),
                header_refs=dict(_mcp_header_refs.get(server.id) or {}),
                tags=list(server.tags),
                tools=[tool.model_dump(by_alias=True) for tool in server.tools],
                status=server.status,
                enabled=server.enabled,
            )
            write_mcp_file(mcp_dir() / f"{server.id}.yaml", record)
            _mcp_header_refs[server.id] = record.header_refs
            _mcp_servers[server.id] = server.model_copy(update={"headers": record.headers})
        return
    replace_mcp_servers({
        server_id: server.model_dump(by_alias=True)
        for server_id, server in _mcp_servers.items()
        if server_id not in _builtin_mcp_servers
    })


def _install_mcp_registry() -> None:
    """第一次访问当前租户时再加载用户 MCP。"""

    global _mcp_servers, _mcp_header_refs
    from src.tenancy.registry import TenantDictProxy, TenantScoped

    _mcp_servers = TenantDictProxy(TenantScoped(lambda: dict(_builtin_mcp_servers)), _load_user_mcp_servers)  # type: ignore[assignment]
    _mcp_header_refs = TenantDictProxy(TenantScoped(dict), None)  # type: ignore[assignment]


_install_mcp_registry()


def _persist_if_user_server(server_id: str) -> None:
    if server_id not in _builtin_mcp_servers:
        _save_user_mcp_servers()


def resolve_mcp_runtime_servers(
    tool_ids: list[str],
    *,
    allowed_server_ids: set[str] | frozenset[str] | None = None,
) -> list[dict[str, object]]:
    """Resolve selected MCP tool ids into server specs for Agent runtime."""

    _refresh_mcp_files()
    runtime_servers = []
    for server in _mcp_servers.values():
        headers = resolve_headers(server.headers, _mcp_header_refs.get(server.id) or {})
        runtime_servers.append(server.model_copy(update={"headers": headers}))
    return _resolve_mcp_runtime_servers(
        tool_ids,
        runtime_servers,
        allowed_server_ids=allowed_server_ids,
        builtin_server_ids=set(_builtin_mcp_servers),
    )


