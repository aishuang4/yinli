"""Project API。身份是 slug，不再提供 Workspace。"""

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from src.storage.database import (
    add_project_folder,
    create_project,
    delete_project,
    get_project,
    list_projects,
    update_project,
)
from src.storage.project_records import ProjectNameError, assert_directory_grant
from src.tenancy.context import is_hosted_mode


router = APIRouter()
AccessMode = Literal["read", "readwrite"]


class ProjectFolderRecord(BaseModel):
    """一条目录授权。"""

    path: str
    access_mode: AccessMode = Field(alias="accessMode")
    is_primary: bool = Field(alias="isPrimary")
    granted_at: str | None = Field(default=None, alias="grantedAt")

    model_config = {"populate_by_name": True}


class ProjectRecord(BaseModel):
    """Project API 记录。"""

    slug: str
    name: str
    primary_path: str = Field(alias="primaryPath")
    color: str | None = None
    icon: str | None = None
    status: str = "active"
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")
    archived_at: str | None = Field(default=None, alias="archivedAt")
    folders: list[ProjectFolderRecord] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class ProjectFolderInput(BaseModel):
    """创建时附带的额外目录。"""

    path: str
    access_mode: AccessMode = Field(default="read", alias="accessMode")

    model_config = {"populate_by_name": True}


class ProjectCreateRequest(BaseModel):
    """创建 Project。名称创建后不可改，slug 由名称派生。"""

    name: str
    primary_path: str | None = Field(default=None, alias="primaryPath")
    folders: list[ProjectFolderInput] = Field(default_factory=list)
    color: str | None = None
    icon: str | None = None

    model_config = {"populate_by_name": True}


class ProjectPatchRequest(BaseModel):
    """只允许改颜色和图标。"""

    color: str | None = None
    icon: str | None = None


class ProjectFolderCreateRequest(BaseModel):
    """追加目录授权。"""

    path: str
    access_mode: AccessMode = Field(default="read", alias="accessMode")

    model_config = {"populate_by_name": True}


def _name_error(exc: ProjectNameError) -> HTTPException:
    return HTTPException(status_code=422, detail=str(exc))


def _require_project(slug: str) -> dict[str, Any]:
    project = get_project(slug)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.get("/projects", response_model=list[ProjectRecord])
async def list_project_records(
    include_archived: bool = Query(default=False, alias="includeArchived"),
) -> list[ProjectRecord]:
    """列出 Project。"""

    return [ProjectRecord.model_validate(item) for item in list_projects(include_archived=include_archived)]


@router.post("/projects", response_model=ProjectRecord, status_code=201)
async def create_project_record(data: ProjectCreateRequest) -> ProjectRecord:
    """按名称创建 Project。托管模式忽略客户端路径。"""

    payload: dict[str, Any] = {
        "name": data.name,
        "color": data.color,
        "icon": data.icon,
        "folders": [item.model_dump(by_alias=True) for item in data.folders],
    }
    if not is_hosted_mode() and data.primary_path:
        try:
            payload["primaryPath"] = str(assert_directory_grant(data.primary_path))
        except ProjectNameError as exc:
            raise _name_error(exc) from exc
    try:
        created = create_project(payload)
    except ProjectNameError as exc:
        raise _name_error(exc) from exc
    return ProjectRecord.model_validate(created)


@router.get("/projects/{slug}", response_model=ProjectRecord)
async def get_project_record(slug: str) -> ProjectRecord:
    """读取 Project。"""

    return ProjectRecord.model_validate(_require_project(slug))


@router.patch("/projects/{slug}", response_model=ProjectRecord)
async def patch_project_record(slug: str, data: ProjectPatchRequest) -> ProjectRecord:
    """更新颜色或图标。名称与目录不在这里改。"""

    _require_project(slug)
    changes = data.model_dump(exclude_unset=True)
    updated = update_project(slug, changes)
    if updated is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return ProjectRecord.model_validate(updated)


@router.post("/projects/{slug}/archive", response_model=ProjectRecord)
async def archive_project_record(slug: str) -> ProjectRecord:
    """归档 Project。默认项目不能归档。"""

    _require_project(slug)
    try:
        updated = update_project(slug, {"status": "archived"})
    except ProjectNameError as exc:
        raise _name_error(exc) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return ProjectRecord.model_validate(updated)


@router.delete("/projects/{slug}", status_code=204)
async def delete_project_record(slug: str) -> None:
    """删除非默认 Project。"""

    try:
        deleted = delete_project(slug)
    except ProjectNameError as exc:
        raise _name_error(exc) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="Project not found")


@router.post("/projects/{slug}/folders", response_model=ProjectRecord, status_code=201)
async def add_project_folder_record(slug: str, data: ProjectFolderCreateRequest) -> ProjectRecord:
    """追加目录授权。托管模式固定单目录，返回 405。"""

    if is_hosted_mode():
        raise HTTPException(status_code=405, detail="托管模式不接受额外目录")
    _require_project(slug)
    try:
        updated = add_project_folder(slug, data.path, data.access_mode)
    except ProjectNameError as exc:
        raise _name_error(exc) from exc
    if updated is None:
        raise HTTPException(status_code=409, detail="Project 已归档，不能追加目录")
    return ProjectRecord.model_validate(updated)
