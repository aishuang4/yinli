"""Configurable local tools."""

from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

from src.generated_file_preview import build_preview_payload, guess_media_type

from src.catalog.http_files import HttpToolFileRecord, discover_http_files, write_http_file
from src.catalog.migrate_mcp_http import ensure_http_file_catalog, is_http_catalog_migrated
from src.config.runtime_paths import tenant_root
from src.catalog.secrets import delete_named_secret, load_http_auth, redact_headers, resolve_headers, store_http_auth
from src.scheduler.service import ScheduledJobService
from src.server.routes.agents import remove_knowledge_tools_from_agents
from src.project_files import resolve_project_session_artifact
from src.storage.records.resource_catalog_records import (
    delete_http_tool as db_delete_http_tool,
    list_http_tools as db_list_http_tools,
)
from src.storage.config.json_document_store import (
    delete_json_record,
    list_json_records,
    upsert_json_record,
)
from src.storage.records.project_records import (
    get_chat_session_location,
    get_project,
)
from src.storage.records.resource_catalog_records import upsert_http_tool
from src.tools.configured_http_tool import execute_configured_http_tool


router = APIRouter()
_job_service = ScheduledJobService()

KNOWLEDGE_TOOLS_NAMESPACE = "knowledge_tools"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_http_url(value: str) -> str:
    """创建/更新 HTTP 工具时拒绝非 http(s) 绝对地址。"""

    cleaned = str(value or "").strip()
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("URL must be absolute and start with http:// or https://")
    return cleaned


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
    kind: Literal["markdown", "text", "csv", "image", "pdf", "docx", "xlsx", "binary"] = "text"
    media_type: str = Field(default="", alias="mediaType")
    content: str = ""
    truncated: bool = False

    model_config = {"populate_by_name": True, "ser_json_by_alias": True}


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

    model_config = {"populate_by_name": True}

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        return _require_http_url(value)


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

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _require_http_url(value)


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
    platforms: list[str] = Field(default_factory=list)
    platform_notes: list[str] = Field(default_factory=list, alias="platformNotes")

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


def _http_response_from_file(record: HttpToolFileRecord, *, for_runtime: bool) -> HttpToolResponse:
    headers = resolve_headers(record.headers, record.header_refs) if for_runtime else redact_headers(record.headers)
    auth_config = load_http_auth(record.auth_ref) if for_runtime else {}
    return HttpToolResponse.model_validate({
        "id": record.id,
        "name": record.name,
        "description": record.description,
        "category": record.category,
        "method": record.method,
        "url": record.url,
        "authType": record.auth_type,
        "authConfig": auth_config,
        "headers": headers,
        "queryTemplate": record.query_template,
        "bodyTemplate": record.body_template,
        "parameterSchema": record.parameter_schema,
        "parameterInstructions": record.parameter_instructions,
        "responseMode": record.response_mode,
        "responsePath": record.response_path,
        "readonly": record.readonly,
        "approvalPolicy": record.approval_policy,
        "enabled": record.enabled,
        "createdAt": record.created_at or _utc_now(),
        "updatedAt": record.updated_at or _utc_now(),
    })


def _http_tool_records(*, for_runtime: bool = False) -> list[HttpToolResponse]:
    _migrate_legacy_knowledge_tools_to_http()
    ensure_http_file_catalog()
    by_id: dict[str, HttpToolResponse] = {}
    for item in db_list_http_tools():
        tool = HttpToolResponse.model_validate(item)
        if not for_runtime:
            tool = tool.model_copy(update={"auth_config": {}, "headers": redact_headers(tool.headers)})
        by_id[tool.id] = tool
    for record in discover_http_files():
        by_id[record.id] = _http_response_from_file(record, for_runtime=for_runtime)
    return list(by_id.values())


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
        for item in _http_tool_records(for_runtime=True)
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
            "platforms": ["macOS", "Windows", "Linux"],
        },
        {
            "name": "Grep",
            "description": "按关键字搜索文件内容；未安装 ripgrep 时自动使用内置搜索。",
            "category": "filesystem",
            "action": "read",
            "risk": "low",
            "readonly": True,
            "defaultEnabled": True,
            "platforms": ["macOS", "Windows", "Linux"],
            "platformNotes": ["Windows 未安装 rg.exe 时自动降级为 Python 搜索。"],
        },
        {
            "name": "Glob",
            "description": "按文件名模式查找文件。",
            "category": "filesystem",
            "action": "read",
            "risk": "low",
            "readonly": True,
            "defaultEnabled": True,
            "platforms": ["macOS", "Windows", "Linux"],
        },
        {
            "name": "Write",
            "description": "创建或覆盖文件。",
            "category": "filesystem",
            "action": "write",
            "risk": "high",
            "readonly": False,
            "defaultEnabled": True,
            "platforms": ["macOS", "Windows", "Linux"],
        },
        {
            "name": "Edit",
            "description": "修改已有文件内容。",
            "category": "filesystem",
            "action": "write",
            "risk": "high",
            "readonly": False,
            "defaultEnabled": True,
            "platforms": ["macOS", "Windows", "Linux"],
        },
        {
            "name": "Bash",
            "description": "执行 Shell 命令；黑名单仅拦截明显误操作，强隔离需使用 Docker 沙盒。",
            "category": "execution",
            "action": "execute",
            "risk": "high",
            "readonly": False,
            "defaultEnabled": True,
            "platforms": ["macOS", "Windows", "Linux"],
            "platformNotes": ["Windows 使用 cmd.exe；macOS/Linux 使用 sh。"],
        },
        {
            "name": "HttpRequest",
            "description": "通用临时 HTTP 请求工具，受网络权限控制。",
            "category": "network",
            "action": "execute",
            "risk": "medium",
            "readonly": False,
            "defaultEnabled": True,
            "platforms": ["macOS", "Windows", "Linux"],
        },
        {
            "name": "WebFetch",
            "description": "安全读取公网、本机或内网网页并提取正文，不提供网络搜索。",
            "category": "network",
            "action": "read",
            "risk": "medium",
            "readonly": True,
            "defaultEnabled": True,
            "platforms": ["macOS", "Windows", "Linux"],
        },
        {
            "name": "AskUser",
            "description": "向用户发起人工补充信息或确认。",
            "category": "hitl",
            "action": "approve",
            "risk": "low",
            "readonly": True,
            "defaultEnabled": True,
            "platforms": ["macOS", "Windows", "Linux"],
        },
    ]
    return [SystemToolResponse.model_validate(item) for item in records]


def _resolve_downloadable_file(*, session_id: str, path: str) -> Path:
    """解析指定会话的可下载产物，拒绝会话目录外的路径。"""

    location = get_chat_session_location(session_id)
    if location is None:
        raise HTTPException(status_code=404, detail="Session not found")
    project = get_project(str(location.get("projectId") or ""))
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
    _job_service.pause_jobs_for_catalog_change(resource_type="http_tool", resource_id=tool_id)
    remove_knowledge_tools_from_agents({tool_id})


@router.get("/http", response_model=list[HttpToolResponse])
async def list_http_tool_configs() -> list[HttpToolResponse]:
    """列出通用 HTTP 工具。"""

    return list(_http_tool_records())


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
    if is_http_catalog_migrated():
        file_record = HttpToolFileRecord(
            id=str(record["id"]),
            name=str(record["name"]),
            description=str(record.get("description") or ""),
            category=str(record.get("category") or "custom"),
            method=str(record.get("method") or "POST"),
            url=str(record["url"]),
            auth_type=str(record.get("authType") or "none"),
            headers=dict(record.get("headers") or {}),
            query_template=dict(record.get("queryTemplate") or {}),
            body_template=dict(record.get("bodyTemplate") or {}),
            parameter_schema=dict(record.get("parameterSchema") or {}),
            parameter_instructions=str(record.get("parameterInstructions") or ""),
            response_mode=str(record.get("responseMode") or "full"),
            response_path=str(record.get("responsePath") or ""),
            readonly=bool(record.get("readonly", True)),
            approval_policy=str(record.get("approvalPolicy") or "inherit"),
            enabled=bool(record.get("enabled", True)),
            created_at=now,
            updated_at=now,
        )
        file_record.auth_ref = store_http_auth(file_record.id, dict(record.get("authConfig") or {}))
        write_http_file(
            tenant_root() / "tools" / f"{file_record.id}.yaml",
            file_record,
        )
        return _http_response_from_file(file_record, for_runtime=False)
    return HttpToolResponse.model_validate(upsert_http_tool(record))


@router.put("/http/{tool_id}", response_model=HttpToolResponse)
async def update_http_tool_config(tool_id: str, data: HttpToolUpdate) -> HttpToolResponse:
    """更新一个通用 HTTP 工具配置。"""

    existing = next((item for item in _http_tool_records(for_runtime=True) if item.id == tool_id), None)
    if existing is None:
        raise HTTPException(status_code=404, detail="HTTP tool not found")
    next_data = existing.model_dump(by_alias=True)
    next_data.update(data.model_dump(exclude_unset=True, by_alias=True))
    next_data["updatedAt"] = _utc_now()
    if is_http_catalog_migrated():
        file_record = HttpToolFileRecord(
            id=tool_id,
            name=str(next_data["name"]),
            description=str(next_data.get("description") or ""),
            category=str(next_data.get("category") or "custom"),
            method=str(next_data.get("method") or "POST"),
            url=str(next_data["url"]),
            auth_type=str(next_data.get("authType") or "none"),
            headers=dict(next_data.get("headers") or {}),
            query_template=dict(next_data.get("queryTemplate") or {}),
            body_template=dict(next_data.get("bodyTemplate") or {}),
            parameter_schema=dict(next_data.get("parameterSchema") or {}),
            parameter_instructions=str(next_data.get("parameterInstructions") or ""),
            response_mode=str(next_data.get("responseMode") or "full"),
            response_path=str(next_data.get("responsePath") or ""),
            readonly=bool(next_data.get("readonly", True)),
            approval_policy=str(next_data.get("approvalPolicy") or "inherit"),
            enabled=bool(next_data.get("enabled", True)),
            created_at=str(next_data.get("createdAt") or ""),
            updated_at=str(next_data.get("updatedAt") or ""),
        )
        incoming_auth = data.auth_config if data.auth_config is not None else existing.auth_config
        file_record.auth_ref = store_http_auth(file_record.id, dict(incoming_auth or {}))
        write_http_file(
            tenant_root() / "tools" / f"{file_record.id}.yaml",
            file_record,
        )
        return _http_response_from_file(file_record, for_runtime=False)
    return HttpToolResponse.model_validate(upsert_http_tool(next_data))


@router.delete("/http/{tool_id}", status_code=204)
async def delete_http_tool_config(tool_id: str) -> None:
    """删除一个通用 HTTP 工具配置。"""

    existing = next((item for item in _http_tool_records() if item.id == tool_id), None)
    deleted = db_delete_http_tool(tool_id)
    yaml_path = tenant_root() / "tools" / f"{tool_id}.yaml"
    file_existed = yaml_path.exists()
    if file_existed:
        yaml_path.unlink()
    try:
        delete_named_secret(f"secret:http:{tool_id}:auth")
    except Exception:
        pass
    if not deleted and not file_existed:
        raise HTTPException(status_code=404, detail="HTTP tool not found")
    _job_service.pause_jobs_for_catalog_change(resource_type="http_tool", resource_id=tool_id)
    remove_knowledge_tools_from_agents({tool_id})


@router.post("/http/{tool_id}/debug", response_model=HttpToolDebugResponse)
async def debug_http_tool_config(tool_id: str, data: HttpToolDebugRequest) -> HttpToolDebugResponse:
    """用给定参数调试一个通用 HTTP 工具。"""

    existing = next((item for item in _http_tool_records(for_runtime=True) if item.id == tool_id), None)
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


@router.get("/files/content")
async def serve_generated_file_content(
    path: str = Query(..., min_length=1),
    session_id: str = Query(..., min_length=1, max_length=128, alias="sessionId"),
) -> FileResponse:
    """以内联方式提供产物，供图片 / PDF 在右侧预览栏展示。"""
    file_path = _resolve_downloadable_file(session_id=session_id, path=path)
    return FileResponse(
        path=str(file_path),
        filename=file_path.name,
        media_type=guess_media_type(file_path.name),
        content_disposition_type="inline",
    )


@router.get("/files/preview", response_model=GeneratedFilePreviewResponse)
async def preview_generated_file(
    path: str = Query(..., min_length=1),
    session_id: str = Query(..., min_length=1, max_length=128, alias="sessionId"),
) -> GeneratedFilePreviewResponse:
    """预览 Agent 工具生成的会话产物。"""
    file_path = _resolve_downloadable_file(session_id=session_id, path=path)
    return GeneratedFilePreviewResponse.model_validate(build_preview_payload(file_path))
