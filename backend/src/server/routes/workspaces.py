"""Workspace / Project 归属与资源绑定 API。"""

import uuid
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from src.sessions.files import save_project_upload
from src.scheduler.service import ScheduledJobService
from src.storage.database import (
    create_project,
    create_workspace,
    get_project,
    get_workspace,
    list_project_resource_bindings,
    list_projects,
    list_workspaces,
    update_project,
    upsert_project_resource_binding,
)


router = APIRouter()
ProjectStatus = Literal["active", "archived"]
_job_service = ScheduledJobService()
_LEGACY_WRITE = HTTPException(
    status_code=410,
    detail={
        "code": "legacy_workspace_retired",
        "message": "Workspace/Project 寫入已停用，不會更改 Session 歸屬或目錄。請改用 Session。",
    },
)


class WorkspaceRecord(BaseModel):
    """Workspace API 记录。"""

    id: str
    name: str
    description: str = ""
    status: str = "active"
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")

    model_config = {"populate_by_name": True}


class WorkspaceCreateRequest(BaseModel):
    """创建 Workspace 请求。"""

    name: str
    description: str = ""


class ProjectRecord(BaseModel):
    """Project API 记录。"""

    id: str
    workspace_id: str = Field(alias="workspaceId")
    name: str
    description: str = ""
    root_path: str = Field(alias="rootPath")
    instructions_file: str = Field(default="", alias="instructionsFile")
    status: str = "active"
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")

    model_config = {"populate_by_name": True}


class ProjectCreateRequest(BaseModel):
    """创建 Project 请求。"""

    name: str
    description: str = ""
    root_path: str = Field(alias="rootPath")
    instructions_file: str = Field(default="", alias="instructionsFile")

    model_config = {"populate_by_name": True}


class WorkspaceBootstrapRequest(ProjectCreateRequest):
    """创建 Workspace 及首个 Project 的组合请求。"""

    workspace_name: str = Field(alias="workspaceName")
    workspace_description: str = Field(default="", alias="workspaceDescription")


class WorkspaceBootstrapResponse(BaseModel):
    """Workspace 初始化结果。"""

    workspace: WorkspaceRecord
    project: ProjectRecord


class ProjectUpdateRequest(BaseModel):
    """更新 Project 请求。"""

    name: str | None = None
    description: str | None = None
    root_path: str | None = Field(default=None, alias="rootPath")
    instructions_file: str | None = Field(default=None, alias="instructionsFile")
    status: ProjectStatus | None = None

    model_config = {"populate_by_name": True}


class ProjectResourceBindingRequest(BaseModel):
    """Project 资源绑定请求。"""

    enabled: bool = True
    config_override: dict[str, Any] = Field(default_factory=dict, alias="configOverride")

    model_config = {"populate_by_name": True}


class ProjectFileUploadResponse(BaseModel):
    """上传后可提供给 Agent 的 Project 文件元数据。"""

    name: str
    path: str
    size: int
    media_type: str = Field(alias="mediaType")

    model_config = {"populate_by_name": True}


def _hosted_project_root(project_id: str) -> str:
    """托管 Project 目录由服务端分配在当前租户内，忽略客户端路径。"""

    from src.config.runtime_paths import projects_root, tenant_root

    root = (projects_root() / project_id).resolve()
    if not root.is_relative_to(tenant_root().resolve()):
        raise HTTPException(status_code=422, detail="Project 目录超出当前租户")
    root.mkdir(parents=True, exist_ok=True)
    return str(root)


def _validated_project_paths(root_path: str, instructions_file: str = "") -> tuple[str, str]:
    """规范化 Project root，并限制 instructions_file 位于项目目录内。"""

    root = Path(root_path).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise HTTPException(status_code=422, detail=f"Project root 不是有效目录: {root}")
    normalized_instructions = instructions_file.strip()
    if normalized_instructions:
        instruction_path = Path(normalized_instructions).expanduser()
        if not instruction_path.is_absolute():
            instruction_path = root / instruction_path
        instruction_path = instruction_path.resolve()
        if not instruction_path.is_relative_to(root):
            raise HTTPException(status_code=422, detail="instructionsFile 必须位于 Project root 内")
        normalized_instructions = str(instruction_path.relative_to(root))
    return str(root), normalized_instructions


@router.get("/workspaces", response_model=list[WorkspaceRecord])
async def list_workspace_records(
    include_archived: bool = Query(default=False, alias="includeArchived"),
) -> list[WorkspaceRecord]:
    """列出 Workspace。"""

    return [WorkspaceRecord.model_validate(item) for item in list_workspaces(include_archived=include_archived)]


@router.post("/workspaces", response_model=WorkspaceRecord, status_code=201)
async def create_workspace_record(data: WorkspaceCreateRequest) -> WorkspaceRecord:
    """舊寫入已停用。"""

    del data
    raise _LEGACY_WRITE


@router.post("/workspaces/bootstrap", response_model=WorkspaceBootstrapResponse, status_code=201)
async def bootstrap_workspace(data: WorkspaceBootstrapRequest) -> WorkspaceBootstrapResponse:
    """舊組合建立已停用，不建立 Workspace 或 Project。"""

    del data
    raise _LEGACY_WRITE


@router.get("/workspaces/{workspace_id}/projects", response_model=list[ProjectRecord])
async def list_workspace_projects(
    workspace_id: str,
    include_archived: bool = Query(default=False, alias="includeArchived"),
) -> list[ProjectRecord]:
    """列出指定 Workspace 下的 Project。"""

    if get_workspace(workspace_id) is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return [
        ProjectRecord.model_validate(item)
        for item in list_projects(workspace_id, include_archived=include_archived)
    ]


@router.post("/workspaces/{workspace_id}/projects", response_model=ProjectRecord, status_code=201)
async def create_project_record(workspace_id: str, data: ProjectCreateRequest) -> ProjectRecord:
    """舊 Project 建立已停用。"""

    del workspace_id, data
    raise _LEGACY_WRITE


@router.get("/projects/{project_id}", response_model=ProjectRecord)
async def get_project_record(project_id: str) -> ProjectRecord:
    """读取 Project。"""

    project = get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return ProjectRecord.model_validate(project)


@router.post("/projects/{project_id}/files", response_model=ProjectFileUploadResponse, status_code=201)
async def upload_project_file(
    project_id: str,
    session_id: str = Query(..., min_length=1, max_length=128, alias="sessionId"),
    file: UploadFile = File(...),
) -> ProjectFileUploadResponse:
    """舊 Project 上傳已停用，避免把附件寫進外部目錄。"""

    del project_id, session_id, file
    raise _LEGACY_WRITE


@router.patch("/projects/{project_id}", response_model=ProjectRecord)
async def update_project_record(project_id: str, data: ProjectUpdateRequest) -> ProjectRecord:
    """舊 Project 更新已停用，不能改目錄或歸屬。"""

    del project_id, data
    raise _LEGACY_WRITE


@router.get("/projects/{project_id}/resources")
async def list_project_resources(project_id: str) -> list[dict[str, Any]]:
    """列出 Project 资源绑定。"""

    if get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return list_project_resource_bindings(project_id)


@router.put("/projects/{project_id}/resources/{resource_type}/{resource_id}")
async def put_project_resource(
    project_id: str,
    resource_type: str,
    resource_id: str,
    data: ProjectResourceBindingRequest,
) -> dict[str, Any]:
    """舊 binding 寫入已停用，不暫停 Job，也不擴大授權。"""

    del project_id, resource_type, resource_id, data
    raise _LEGACY_WRITE
