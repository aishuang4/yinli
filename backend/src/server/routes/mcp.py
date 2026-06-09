"""
MCP 管理路由

提供 MCP Server 浏览、安装状态管理和工具列表查询。
MVP 阶段使用内存 registry，后续可迁移到 SQLite 并接入真实 MCP 进程管理。
"""

import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Literal, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import anyio
import httpx
from fastapi import APIRouter, HTTPException
from mcp.client.session import ClientSession
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client
from pydantic import BaseModel, Field, HttpUrl

from src.server.routes.agents import remove_mcp_tools_from_agents
from src.storage.database import (
    list_mcp_servers as db_list_mcp_servers,
    list_json_records,
    migrate_json_list_file,
    replace_mcp_servers,
)

router = APIRouter()

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

    model_config = {"populate_by_name": True}


class McpInstallRequest(BaseModel):
    """安装/配置 MCP Server 请求"""

    enabled: bool = True


class McpRegisterRequest(BaseModel):
    """注册自定义 MCP Server 请求"""

    name: str = Field(..., min_length=1, max_length=80)
    url: HttpUrl
    description: str = Field(default="", max_length=300)
    headers: dict[str, str] = Field(default_factory=dict)


DATA_DIR = "data"
USER_MCP_REGISTRY_FILE = os.path.join(DATA_DIR, "mcp_servers.json")
MCP_NAMESPACE = "mcp_servers"
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
                detail=f"Header '{clean_key}' is managed by Agent Workstation",
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
    try:
        return await _fetch_tools_with_mcp_client(url, server_id, request_headers)
    except Exception as mcp_exc:
        try:
            return await anyio.to_thread.run_sync(
                _fetch_remote_mcp_tools_json_fallback,
                url,
                server_id,
                request_headers,
            )
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, ValueError) as fallback_exc:
            raise HTTPException(
                status_code=502,
                detail=f"Failed to fetch MCP tools: {fallback_exc or mcp_exc}",
            ) from fallback_exc


def _load_user_mcp_servers() -> None:
    migrate_json_list_file(MCP_NAMESPACE, USER_MCP_REGISTRY_FILE)
    records = db_list_mcp_servers()
    if not records:
        legacy = list_json_records(MCP_NAMESPACE)
        replace_mcp_servers({str(item["id"]): item for item in legacy if item.get("id")})
        records = db_list_mcp_servers()
    for item in records:
        server = McpServer.model_validate(item)
        _mcp_servers[server.id] = server


def _save_user_mcp_servers() -> None:
    replace_mcp_servers({
        server_id: server.model_dump(by_alias=True)
        for server_id, server in _mcp_servers.items()
        if server_id not in _builtin_mcp_servers
    })


_load_user_mcp_servers()


def _persist_if_user_server(server_id: str) -> None:
    if server_id not in _builtin_mcp_servers:
        _save_user_mcp_servers()


@router.get("", response_model=list[McpServer])
async def list_mcp_servers(
    q: str = "",
    category: str = "",
    installed: Optional[bool] = None,
) -> list[McpServer]:
    """获取 MCP Server 列表，支持搜索、分类和安装状态筛选。"""

    query = q.strip().lower()
    result = list(_mcp_servers.values())

    if query:
        result = [
            server
            for server in result
            if query in server.name.lower()
            or query in server.description.lower()
            or any(query in tag.lower() for tag in server.tags)
            or any(query in tool.name.lower() for tool in server.tools)
        ]

    if category:
        result = [server for server in result if server.category == category]

    if installed is not None:
        result = [
            server
            for server in result
            if (server.status == "installed") is installed
        ]

    return result


@router.get("/categories", response_model=list[str])
async def list_mcp_categories() -> list[str]:
    """获取 MCP Server 分类列表。"""

    return sorted({server.category for server in _mcp_servers.values()})


@router.get("/tools", response_model=list[McpTool])
async def list_installed_mcp_tools() -> list[McpTool]:
    """获取已安装并启用的 MCP 工具列表，用于 Agent 配置选择。"""

    tools: list[McpTool] = []
    for server in _mcp_servers.values():
        if server.status == "installed" and server.enabled:
            tools.extend(server.tools)
    return tools


@router.post("/register", response_model=McpServer, status_code=201)
async def register_mcp_server(data: McpRegisterRequest) -> McpServer:
    """注册自定义 MCP Server，拉取工具列表并保存到本地 registry。"""

    server_id = _unique_server_id(data.name)
    url = str(data.url)
    headers = _normalize_headers(data.headers)
    tools = await _fetch_remote_mcp_tools(url, server_id, headers)
    now = _utc_now()
    server = McpServer.model_validate(
        {
            "id": server_id,
            "name": data.name,
            "description": data.description or f"用户注册的 MCP Server: {data.name}",
            "category": "自定义",
            "transport": "sse",
            "command": "",
            "url": url,
            "homepage": url,
            "headers": headers,
            "tags": ["custom", "remote"],
            "tools": tools,
            "status": "installed",
            "enabled": True,
            "installedAt": now,
            "updatedAt": now,
        }
    )
    _mcp_servers[server.id] = server
    _save_user_mcp_servers()
    return server


@router.get("/{server_id}", response_model=McpServer)
async def get_mcp_server(server_id: str) -> McpServer:
    """获取单个 MCP Server 详情。"""

    if server_id not in _mcp_servers:
        raise HTTPException(status_code=404, detail="MCP server not found")
    return _mcp_servers[server_id]


@router.post("/{server_id}/install", response_model=McpServer)
async def install_mcp_server(
    server_id: str,
    data: McpInstallRequest,
) -> McpServer:
    """标记 MCP Server 为已安装，并设置启用状态。"""

    if server_id not in _mcp_servers:
        raise HTTPException(status_code=404, detail="MCP server not found")

    server = _mcp_servers[server_id].model_copy(
        update={
            "status": "installed",
            "enabled": data.enabled,
            "installed_at": _mcp_servers[server_id].installed_at or _utc_now(),
            "updated_at": _utc_now(),
        }
    )
    _mcp_servers[server_id] = server
    _persist_if_user_server(server_id)
    return server


@router.post("/{server_id}/uninstall", response_model=McpServer)
async def uninstall_mcp_server(server_id: str) -> McpServer:
    """卸载 MCP Server。"""

    if server_id not in _mcp_servers:
        raise HTTPException(status_code=404, detail="MCP server not found")

    server = _mcp_servers[server_id].model_copy(
        update={
            "status": "available",
            "enabled": False,
            "installed_at": None,
            "updated_at": _utc_now(),
        }
    )
    _mcp_servers[server_id] = server
    _persist_if_user_server(server_id)
    return server


@router.delete("/{server_id}", status_code=204)
async def delete_mcp_server(server_id: str) -> None:
    """删除用户注册的 MCP Server。内置 Server 只能卸载，不能删除。"""

    if server_id not in _mcp_servers:
        raise HTTPException(status_code=404, detail="MCP server not found")
    if server_id in _builtin_mcp_servers:
        raise HTTPException(status_code=409, detail="Built-in MCP server cannot be deleted")

    server = _mcp_servers.pop(server_id)
    remove_mcp_tools_from_agents({tool.id for tool in server.tools})
    _save_user_mcp_servers()


@router.post("/{server_id}/enable", response_model=McpServer)
async def set_mcp_server_enabled(
    server_id: str,
    data: McpInstallRequest,
) -> McpServer:
    """启用或停用已安装的 MCP Server。"""

    if server_id not in _mcp_servers:
        raise HTTPException(status_code=404, detail="MCP server not found")

    current = _mcp_servers[server_id]
    if current.status != "installed":
        raise HTTPException(status_code=409, detail="MCP server is not installed")

    server = current.model_copy(
        update={
            "enabled": data.enabled,
            "updated_at": _utc_now(),
        }
    )
    _mcp_servers[server_id] = server
    _persist_if_user_server(server_id)
    return server
