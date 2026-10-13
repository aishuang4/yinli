"""MCP 管理路由。目录和远程拉取在 src.mcp.registry。"""

from typing import Annotated, Optional

from fastapi import APIRouter, HTTPException, Query

from src.scheduler.service import ScheduledJobService
from src.server.routes.agents import remove_mcp_tools_from_agents
from src.mcp.registry import (
    datetime,
    timezone,
    Any,
    Literal,
    Optional,
    HTTPError,
    URLError,
    Request,
    urlopen,
    HTTPException,
    ClientSession,
    sse_client,
    streamable_http_client,
    BaseModel,
    Field,
    HttpUrl,
    McpFileRecord,
    discover_mcp_files,
    write_mcp_file,
    ensure_mcp_file_catalog,
    is_mcp_catalog_migrated,
    mcp_dir,
    delete_named_secret,
    resolve_headers,
    _resolve_mcp_runtime_servers,
    list_json_records,
    db_list_mcp_servers,
    migrate_json_list_file,
    replace_mcp_servers,
    McpTransport,
    McpInstallStatus,
    McpTool,
    McpServer,
    McpInstallRequest,
    McpRegisterRequest,
    MCP_NAMESPACE,
    RESERVED_MCP_HEADERS,
    _utc_now,
    _slugify,
    _unique_server_id,
    _normalize_headers,
    _tool,
    _server,
    _builtin_mcp_servers,
    _mcp_servers,
    _mcp_header_refs,
    _normalize_remote_tools,
    _post_json,
    _get_json,
    _parse_mcp_http_response,
    _extract_tools_from_response,
    _fetch_tools_with_mcp_client,
    _fetch_remote_mcp_tools_json_fallback,
    _fetch_remote_mcp_tools,
    _server_from_file,
    _refresh_mcp_files,
    _load_user_mcp_servers,
    _save_user_mcp_servers,
    _persist_if_user_server,
    resolve_mcp_runtime_servers,
)

router = APIRouter()
_job_service = ScheduledJobService()

@router.get("", response_model=list[McpServer])
async def list_mcp_servers(
    q: str = "",
    category: str = "",
    installed: Optional[bool] = None,
    workspace_id: Annotated[str, Query(alias="workspaceId")] = "default",
) -> list[McpServer]:
    """获取 MCP Server 列表，支持搜索、分类和安装状态筛选。"""

    _refresh_mcp_files()
    query = q.strip().lower()
    result = [
        server
        for server in _mcp_servers.values()
        if server.id in _builtin_mcp_servers or server.workspace_id == workspace_id
    ]

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
async def list_installed_mcp_tools(
    workspace_id: Annotated[str, Query(alias="workspaceId")] = "default",
) -> list[McpTool]:
    """获取已安装并启用的 MCP 工具列表，用于 Agent 配置选择。"""

    tools: list[McpTool] = []
    for server in _mcp_servers.values():
        if (
            server.status == "installed"
            and server.enabled
            and (server.id in _builtin_mcp_servers or server.workspace_id == workspace_id)
        ):
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
            "workspaceId": data.workspace_id,
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
    _job_service.pause_jobs_for_catalog_change(resource_type="mcp", resource_id=server_id)
    return server


@router.delete("/{server_id}", status_code=204)
async def delete_mcp_server(server_id: str) -> None:
    """删除用户注册的 MCP Server。内置 Server 只能卸载，不能删除。"""

    if server_id not in _mcp_servers:
        raise HTTPException(status_code=404, detail="MCP server not found")
    if server_id in _builtin_mcp_servers:
        raise HTTPException(status_code=409, detail="Built-in MCP server cannot be deleted")

    server = _mcp_servers.pop(server_id)
    refs = _mcp_header_refs.pop(server_id, {})
    for ref in refs.values():
        try:
            delete_named_secret(ref)
        except Exception:
            pass
    yaml_path = mcp_dir(server.workspace_id) / f"{server.id}.yaml"
    if yaml_path.exists():
        yaml_path.unlink()
    _job_service.pause_jobs_for_catalog_change(resource_type="mcp", resource_id=server_id)
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
    if not data.enabled:
        _job_service.pause_jobs_for_catalog_change(resource_type="mcp", resource_id=server_id)
    return server
