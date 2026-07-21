"""Configurable local tools."""

from datetime import datetime, timezone
from pathlib import Path
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from src.server.routes.agents import remove_knowledge_tools_from_agents
from src.project_files import resolve_project_session_artifact
from src.storage.database import (
    delete_json_record,
    delete_http_tool as db_delete_http_tool,
    get_chat_session_ownership,
    get_project,
    list_http_tools as db_list_http_tools,
    list_json_records,
    upsert_http_tool,
    upsert_json_record,
)
from src.tools.configured_http_tool import execute_configured_http_tool


router = APIRouter()

KNOWLEDGE_TOOLS_NAMESPACE = "knowledge_tools"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_payload() -> dict[str, Any]:
    return {
        "query": "",
        "filename_pattern": "",
        "filterCategories": [],
        "searchMode": "",
    }


class KnowledgeToolCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    description: str = Field(default="检索外部知识库并返回命中文档片段。", max_length=1000)
    url: str = Field(default="http://localhost:8080/search/knowledgeBaseSearch")
    authorization: str = Field(default="", description="Authorization header value")
    headers: dict[str, str] = Field(default_factory=dict)
    default_payload: dict[str, Any] = Field(default_factory=_default_payload, alias="defaultPayload")
    parameter_instructions: str = Field(
        default=(
            "参数说明：query 是检索问题；filename_pattern 用于按文件名过滤；"
            "filterCategories 是分类过滤数组；searchMode 是检索模式。"
        ),
        alias="parameterInstructions",
    )
    enabled: bool = True

    model_config = {"populate_by_name": True}


class KnowledgeToolUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=1000)
    url: str | None = None
    authorization: str | None = None
    headers: dict[str, str] | None = None
    default_payload: dict[str, Any] | None = Field(default=None, alias="defaultPayload")
    parameter_instructions: str | None = Field(default=None, alias="parameterInstructions")
    enabled: bool | None = None

    model_config = {"populate_by_name": True}


class KnowledgeToolResponse(KnowledgeToolCreate):
    id: str
    created_at: str
    updated_at: str


class GeneratedFilePreviewResponse(BaseModel):
    path: str
    name: str
    size: int
    content: str
    truncated: bool = False


class HttpToolBase(BaseModel):
    """通用 HTTP 工具配置。"""

    name: str = Field(..., min_length=1, max_length=80)
    description: str = Field(default="", max_length=1000)
    category: str = Field(default="custom", max_length=60)
    method: str = Field(default="POST", pattern="^(GET|POST|PUT|PATCH|DELETE)$")
    url: str = Field(..., min_length=1)
    auth_type: str = Field(default="none", alias="authType")
    auth_config: dict[str, Any] = Field(default_factory=dict, alias="authConfig")
    headers: dict[str, str] = Field(default_factory=dict)
    query_template: dict[str, Any] = Field(default_factory=dict, alias="queryTemplate")
    body_template: dict[str, Any] = Field(default_factory=dict, alias="bodyTemplate")
    parameter_schema: dict[str, Any] = Field(default_factory=dict, alias="parameterSchema")
    parameter_instructions: str = Field(default="", alias="parameterInstructions")
    response_mode: str = Field(default="full", alias="responseMode")
    response_path: str = Field(default="", alias="responsePath")
    readonly: bool = True
    approval_policy: str = Field(default="inherit", alias="approvalPolicy")
    enabled: bool = True
    workspace_id: str = Field(default="default", alias="workspaceId")

    model_config = {"populate_by_name": True}


class HttpToolCreate(HttpToolBase):
    pass


class HttpToolUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=1000)
    category: str | None = Field(default=None, max_length=60)
    method: str | None = Field(default=None, pattern="^(GET|POST|PUT|PATCH|DELETE)$")
    url: str | None = Field(default=None, min_length=1)
    auth_type: str | None = Field(default=None, alias="authType")
    auth_config: dict[str, Any] | None = Field(default=None, alias="authConfig")
    headers: dict[str, str] | None = None
    query_template: dict[str, Any] | None = Field(default=None, alias="queryTemplate")
    body_template: dict[str, Any] | None = Field(default=None, alias="bodyTemplate")
    parameter_schema: dict[str, Any] | None = Field(default=None, alias="parameterSchema")
    parameter_instructions: str | None = Field(default=None, alias="parameterInstructions")
    response_mode: str | None = Field(default=None, alias="responseMode")
    response_path: str | None = Field(default=None, alias="responsePath")
    readonly: bool | None = None
    approval_policy: str | None = Field(default=None, alias="approvalPolicy")
    enabled: bool | None = None

    model_config = {"populate_by_name": True}


class HttpToolResponse(HttpToolBase):
    id: str
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")

    model_config = {"populate_by_name": True}


class HttpToolDebugRequest(BaseModel):
    """HTTP 工具调试请求。"""

    parameters: dict[str, Any] = Field(default_factory=dict)


class HttpToolDebugResponse(BaseModel):
    """HTTP 工具调试响应。"""

    ok: bool
    result: dict[str, Any] = Field(default_factory=dict)
    error: str = ""


class SystemToolResponse(BaseModel):
    name: str
    description: str
    category: str
    action: str
    risk: str
    readonly: bool
    default_enabled: bool = Field(alias="defaultEnabled")

    model_config = {"populate_by_name": True}


def _records() -> list[KnowledgeToolResponse]:
    return [
        KnowledgeToolResponse.model_validate(item)
        for item in list_json_records(KNOWLEDGE_TOOLS_NAMESPACE)
    ]


def get_enabled_knowledge_tools(tool_ids: list[str]) -> list[dict[str, Any]]:
    selected = set(tool_ids)
    return [
        item.model_dump(by_alias=True)
        for item in _records()
        if item.enabled and item.id in selected
    ]


def _http_tool_records() -> list[HttpToolResponse]:
    _migrate_legacy_knowledge_tools_to_http()
    return [HttpToolResponse.model_validate(item) for item in db_list_http_tools()]


def _migrate_legacy_knowledge_tools_to_http() -> None:
    """把旧知识库工具按原 ID 投影成通用 HTTP 工具模板。"""

    existing_ids = {str(item.get("id") or "") for item in db_list_http_tools()}
    for legacy in _records():
        if legacy.id in existing_ids:
            continue
        authorization = legacy.authorization.strip()
        token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else authorization
        auth_type = "bearer" if token else "none"
        now = _utc_now()
        upsert_http_tool({
            "id": legacy.id,
            "name": legacy.name,
            "description": legacy.description,
            "category": "knowledge_base",
            "method": "POST",
            "url": legacy.url,
            "authType": auth_type,
            "authConfig": {"token": token} if token else {},
            "headers": legacy.headers,
            "queryTemplate": {},
            "bodyTemplate": legacy.default_payload,
            "parameterSchema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "检索问题，通常是用户问题或改写后的查询语句。",
                    },
                    "filename_pattern": {
                        "type": "string",
                        "description": "可选文件名过滤。",
                    },
                    "filterCategories": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "可选分类过滤。",
                    },
                    "searchMode": {
                        "type": "string",
                        "description": "可选检索模式。",
                    },
                },
                "required": ["query"],
            },
            "parameterInstructions": legacy.parameter_instructions,
            "responseMode": "full",
            "responsePath": "",
            "readonly": True,
            "approvalPolicy": "inherit",
            "enabled": legacy.enabled,
            "createdAt": legacy.created_at or now,
            "updatedAt": now,
        })


def get_enabled_http_tools(tool_ids: list[str]) -> list[dict[str, Any]]:
    """Return enabled configurable HTTP tools selected by an Agent."""

    selected = set(tool_ids)
    return [
        item.model_dump(by_alias=True)
        for item in _http_tool_records()
        if item.enabled and item.id in selected
    ]


def _system_tool_records() -> list[SystemToolResponse]:
    records = [
        {
            "name": "Read",
            "description": "读取工作区内文本文件内容。",
            "category": "filesystem",
            "action": "read",
            "risk": "low",
            "readonly": True,
            "defaultEnabled": True,
        },
        {
            "name": "Grep",
            "description": "按关键字搜索文件内容。",
            "category": "filesystem",
            "action": "read",
            "risk": "low",
            "readonly": True,
            "defaultEnabled": True,
        },
        {
            "name": "Glob",
            "description": "按文件名模式查找文件。",
            "category": "filesystem",
            "action": "read",
            "risk": "low",
            "readonly": True,
            "defaultEnabled": True,
        },
        {
            "name": "Write",
            "description": "创建或覆盖文件。",
            "category": "filesystem",
            "action": "write",
            "risk": "high",
            "readonly": False,
            "defaultEnabled": True,
        },
        {
            "name": "Edit",
            "description": "修改已有文件内容。",
            "category": "filesystem",
            "action": "write",
            "risk": "high",
            "readonly": False,
            "defaultEnabled": True,
        },
        {
            "name": "Bash",
            "description": "执行 Shell 命令，受权限和黑名单约束。",
            "category": "execution",
            "action": "execute",
            "risk": "high",
            "readonly": False,
            "defaultEnabled": True,
        },
        {
            "name": "HttpRequest",
            "description": "通用临时 HTTP 请求工具，受网络权限控制。",
            "category": "network",
            "action": "execute",
            "risk": "medium",
            "readonly": False,
            "defaultEnabled": True,
        },
        {
            "name": "WebFetch",
            "description": "安全读取公开网页并提取正文，不提供网络搜索。",
            "category": "network",
            "action": "read",
            "risk": "medium",
            "readonly": True,
            "defaultEnabled": True,
        },
        {
            "name": "AskUser",
            "description": "向用户发起人工补充信息或确认。",
            "category": "hitl",
            "action": "approve",
            "risk": "low",
            "readonly": True,
            "defaultEnabled": True,
        },
    ]
    return [SystemToolResponse.model_validate(item) for item in records]


def _resolve_downloadable_file(*, session_id: str, path: str) -> Path:
    """解析指定会话的可下载产物，拒绝会话目录外的路径。"""

    ownership = get_chat_session_ownership(session_id)
    if ownership is None:
        raise HTTPException(status_code=404, detail="Session not found")
    project = get_project(str(ownership.get("projectId") or ""))
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    project_root = Path(str(project.get("rootPath") or "")).expanduser().resolve()
    if not project_root.is_dir():
        raise HTTPException(status_code=409, detail="Project root 不可用")
    return resolve_project_session_artifact(
        project_root=project_root,
        session_id=session_id,
        relative_path=path,
    )


@router.get("/knowledge-base", response_model=list[KnowledgeToolResponse])
async def list_knowledge_tools() -> list[KnowledgeToolResponse]:
    return _records()


@router.post("/knowledge-base", response_model=KnowledgeToolResponse, status_code=201)
async def create_knowledge_tool(data: KnowledgeToolCreate) -> KnowledgeToolResponse:
    now = _utc_now()
    tool = KnowledgeToolResponse.model_validate({
        **data.model_dump(by_alias=True),
        "id": str(uuid.uuid4()),
        "created_at": now,
        "updated_at": now,
    })
    upsert_json_record(KNOWLEDGE_TOOLS_NAMESPACE, tool.id, tool.model_dump(by_alias=True))
    return tool


@router.put("/knowledge-base/{tool_id}", response_model=KnowledgeToolResponse)
async def update_knowledge_tool(tool_id: str, data: KnowledgeToolUpdate) -> KnowledgeToolResponse:
    existing = next((item for item in _records() if item.id == tool_id), None)
    if existing is None:
        raise HTTPException(status_code=404, detail="Knowledge tool not found")

    update_data = data.model_dump(exclude_unset=True, by_alias=True)
    next_data = existing.model_dump(by_alias=True)
    next_data.update(update_data)
    next_data["updated_at"] = _utc_now()
    tool = KnowledgeToolResponse.model_validate(next_data)
    upsert_json_record(KNOWLEDGE_TOOLS_NAMESPACE, tool.id, tool.model_dump(by_alias=True))
    return tool


@router.delete("/knowledge-base/{tool_id}", status_code=204)
async def delete_knowledge_tool(tool_id: str) -> None:
    existing = next((item for item in _records() if item.id == tool_id), None)
    if existing is None:
        raise HTTPException(status_code=404, detail="Knowledge tool not found")

    delete_json_record(KNOWLEDGE_TOOLS_NAMESPACE, tool_id)
    remove_knowledge_tools_from_agents({tool_id})


@router.get("/http", response_model=list[HttpToolResponse])
async def list_http_tool_configs(
    workspace_id: Annotated[str, Query(alias="workspaceId")] = "default",
) -> list[HttpToolResponse]:
    """列出通用 HTTP 工具。"""

    return [tool for tool in _http_tool_records() if tool.workspace_id == workspace_id]


@router.post("/http", response_model=HttpToolResponse, status_code=201)
async def create_http_tool_config(data: HttpToolCreate) -> HttpToolResponse:
    """创建一个通用 HTTP 工具配置。"""

    now = _utc_now()
    record = {
        **data.model_dump(by_alias=True),
        "id": str(uuid.uuid4()),
        "createdAt": now,
        "updatedAt": now,
    }
    return HttpToolResponse.model_validate(upsert_http_tool(record))


@router.put("/http/{tool_id}", response_model=HttpToolResponse)
async def update_http_tool_config(tool_id: str, data: HttpToolUpdate) -> HttpToolResponse:
    """更新一个通用 HTTP 工具配置。"""

    existing = next((item for item in _http_tool_records() if item.id == tool_id), None)
    if existing is None:
        raise HTTPException(status_code=404, detail="HTTP tool not found")
    next_data = existing.model_dump(by_alias=True)
    next_data.update(data.model_dump(exclude_unset=True, by_alias=True))
    next_data["updatedAt"] = _utc_now()
    return HttpToolResponse.model_validate(upsert_http_tool(next_data))


@router.delete("/http/{tool_id}", status_code=204)
async def delete_http_tool_config(tool_id: str) -> None:
    """删除一个通用 HTTP 工具配置。"""

    if not db_delete_http_tool(tool_id):
        raise HTTPException(status_code=404, detail="HTTP tool not found")
    remove_knowledge_tools_from_agents({tool_id})


@router.post("/http/{tool_id}/debug", response_model=HttpToolDebugResponse)
async def debug_http_tool_config(tool_id: str, data: HttpToolDebugRequest) -> HttpToolDebugResponse:
    """用给定参数调试一个通用 HTTP 工具。"""

    existing = next((item for item in _http_tool_records() if item.id == tool_id), None)
    if existing is None:
        raise HTTPException(status_code=404, detail="HTTP tool not found")
    try:
        result = await execute_configured_http_tool(
            existing.model_dump(by_alias=True),
            data.parameters,
        )
    except Exception as exc:
        return HttpToolDebugResponse(ok=False, error=str(exc))
    return HttpToolDebugResponse(ok=int(result.get("status_code") or 0) < 400, result=result)


@router.get("/system", response_model=list[SystemToolResponse])
async def list_system_tools() -> list[SystemToolResponse]:
    """列出系统内置工具。"""

    return _system_tool_records()


@router.get("/files/download")
async def download_generated_file(
    path: str = Query(..., min_length=1),
    session_id: str = Query(..., min_length=1, max_length=128, alias="sessionId"),
) -> FileResponse:
    """下载 Agent 工具生成的本地文件。"""
    file_path = _resolve_downloadable_file(session_id=session_id, path=path)
    return FileResponse(
        path=str(file_path),
        filename=file_path.name,
        media_type="application/octet-stream",
    )


@router.get("/files/preview", response_model=GeneratedFilePreviewResponse)
async def preview_generated_file(
    path: str = Query(..., min_length=1),
    session_id: str = Query(..., min_length=1, max_length=128, alias="sessionId"),
) -> GeneratedFilePreviewResponse:
    """预览 Agent 工具生成的文本文件。"""
    file_path = _resolve_downloadable_file(session_id=session_id, path=path)
    max_preview_bytes = 200 * 1024
    size = file_path.stat().st_size
    raw = file_path.read_bytes()[: max_preview_bytes + 1]
    truncated = len(raw) > max_preview_bytes
    if truncated:
        raw = raw[:max_preview_bytes]
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        content = raw.decode("utf-8", errors="replace")
    return GeneratedFilePreviewResponse(
        path=str(file_path),
        name=file_path.name,
        size=size,
        content=content,
        truncated=truncated or size > max_preview_bytes,
    )
