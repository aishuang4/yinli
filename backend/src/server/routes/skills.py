"""Skills 管理路由。

目录、注册和运行时选择在 src.skills.registry。
"""

from io import BytesIO
from typing import Annotated, Optional
from urllib.parse import quote

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pathlib import Path

from src.scheduler.service import ScheduledJobService
from src.storage.records.project_records import get_project
from src.server.routes.agents import remove_skills_from_agents
from src.server.routes.skill_packages import MAX_SKILL_ARCHIVE_BYTES, _split_frontmatter
from src.catalog.skills import discover_skill_packages
from src.skills.registry import (
    Skill,
    SkillInstallRequest,
    SkillRegisterRequest,
    SkillShareRequest,
    _builtin_skills,
    _ensure_skill_name_available,
    _ensure_skill_package,
    _load_user_skills,
    _external_skill_overrides,
    _extract_skill_package,
    _is_share_locked,
    _read_skill_md_from_zip,
    _save_user_skills,
    _skill_export_archive,
    _skill_export_filename,
    _skill_from_markdown,
    _skills,
    _upsert_external_override,
    _utc_now,
    _with_share_flag,
    delete_external_skill,
    delete_registered_skill_files,
    delete_skill_override,
    discover_external_skills,
    find_skill,
    get_installed_skill,
    install_external_skill,
    list_installed_enabled_skills,
    list_skill_usage_details,
    refresh_skill_package,
    register_user_skill,
    reload_skill_cache,
    resolve_installed_skills,
    resolve_runtime_skill_paths,
    uninstall_external_skill,
)

router = APIRouter()
_job_service = ScheduledJobService()


def _project_root(project_slug: str) -> Path | None:
    """当前请求的 Project 主目录。"""

    if not project_slug:
        return None
    project = get_project(project_slug)
    if not project:
        return None
    return Path(str(project.get("primaryPath") or ""))


@router.get("", response_model=list[Skill])
async def list_skills(
    q: str = "",
    category: str = "",
    installed: Optional[bool] = None,
    project_slug: Annotated[str, Query(alias="projectSlug")] = "",
) -> list[Skill]:
    """获取 Skill 列表，支持搜索、分类和安装状态筛选。"""

    query = q.strip().lower()
    reload_skill_cache(_project_root(project_slug))
    result = [
        refresh_skill_package(skill)
        for skill in _skills.values()
    ]

    if query:
        result = [
            skill
            for skill in result
            if query in skill.name.lower()
            or query in skill.description.lower()
            or query in skill.trigger.lower()
            or any(query in tag.lower() for tag in skill.tags)
        ]

    if category:
        result = [skill for skill in result if skill.category == category]

    if installed is not None:
        result = [
            skill
            for skill in result
            if (skill.status == "installed") is installed
        ]

    return [_with_share_flag(skill) for skill in result]


@router.get("/categories", response_model=list[str])
async def list_skill_categories() -> list[str]:
    """获取 Skill 分类列表。"""

    return sorted({skill.category for skill in _skills.values()})


@router.get("/enabled", response_model=list[Skill])
async def list_enabled_skills() -> list[Skill]:
    """获取已安装并启用的 Skills，用于 Agent 配置选择。"""

    return [
        _with_share_flag(skill)
        for skill in _skills.values()
        if skill.status == "installed" and skill.enabled
    ]


@router.post("/register", response_model=Skill, status_code=201)
async def register_skill(data: SkillRegisterRequest) -> Skill:
    """注册自定义 Skill 并保存到本地 registry。"""

    return register_user_skill(data)


@router.post("/import", response_model=Skill, status_code=201)
async def import_skill_archive(
    file: UploadFile = File(...),
) -> Skill:
    """导入标准 Skill zip 包，读取 SKILL.md 并注册为已安装启用的 Skill。"""

    filename = file.filename or ""
    if not filename.lower().endswith(".zip"):
        raise HTTPException(status_code=422, detail="Only .zip skill archives are supported")

    raw = await file.read()
    if len(raw) > MAX_SKILL_ARCHIVE_BYTES:
        raise HTTPException(status_code=413, detail="Skill archive is too large")

    skill_path, markdown, archive, members = _read_skill_md_from_zip(raw)
    try:
        draft_skill = _skill_from_markdown(skill_path, markdown)
        _ensure_skill_name_available(draft_skill.name)
        resource_path, resources = _extract_skill_package(
            archive,
            members,
            skill_path,
            draft_skill.name,
        )
        _skill_from_markdown(skill_path, markdown, resource_path, resources)
        reload_skill_cache()
        wanted = str(Path(resource_path).resolve())
        for skill in _skills.values():
            if skill.resource_path and str(Path(skill.resource_path).resolve()) == wanted:
                return skill
        raise HTTPException(status_code=500, detail="导入后的 Skill 没有被扫描到")
    finally:
        archive.close()


@router.get("/{skill_id}", response_model=Skill)
async def get_skill(
    skill_id: str,
    project_slug: Annotated[str, Query(alias="projectSlug")] = "",
) -> Skill:
    """获取单个 Skill 详情，正文从当前 SKILL.md 重新读取。"""

    skill = find_skill(skill_id, _project_root(project_slug))
    if skill is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    return skill


@router.get("/{skill_id}/export")
async def export_skill(
    skill_id: str,
    project_slug: Annotated[str, Query(alias="projectSlug")] = "",
) -> StreamingResponse:
    """导出标准 Skill zip 包。"""

    skill = find_skill(skill_id, _project_root(project_slug))
    if skill is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    raw = _skill_export_archive(skill)
    filename = _skill_export_filename(skill)
    quoted_filename = quote(filename)
    return StreamingResponse(
        BytesIO(raw),
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{quoted_filename}"; filename*=UTF-8\'\'{quoted_filename}'
            )
        },
    )


@router.post("/{skill_id}/install", response_model=Skill)
async def install_skill(
    skill_id: str,
    data: SkillInstallRequest,
    project_slug: Annotated[str, Query(alias="projectSlug")] = "",
) -> Skill:
    """标记 Skill 为已安装，并设置启用状态。"""

    if skill_id not in _skills:
        current = find_skill(skill_id, _project_root(project_slug))
        if current is None:
            raise HTTPException(status_code=404, detail="Skill not found")
        return install_external_skill(current, enabled=data.enabled)

    _upsert_external_override(skill_id, installed=True, enabled=data.enabled)
    reload_skill_cache(_project_root(project_slug))
    return _skills[skill_id]


@router.post("/{skill_id}/uninstall", response_model=Skill)
async def uninstall_skill(
    skill_id: str,
    project_slug: Annotated[str, Query(alias="projectSlug")] = "",
) -> Skill:
    """卸载 Skill。"""

    if skill_id not in _skills:
        current = find_skill(skill_id, _project_root(project_slug))
        if current is None:
            raise HTTPException(status_code=404, detail="Skill not found")
        updated = uninstall_external_skill(current)
        _job_service.pause_jobs_for_catalog_change(resource_type="skill", resource_id=skill_id)
        return updated

    _upsert_external_override(skill_id, installed=False, enabled=False)
    reload_skill_cache(_project_root(project_slug))
    _job_service.pause_jobs_for_catalog_change(resource_type="skill", resource_id=skill_id)
    return _skills[skill_id]


@router.delete("/{skill_id}", status_code=204)
async def delete_skill(
    skill_id: str,
    project_slug: Annotated[str, Query(alias="projectSlug")] = "",
) -> None:
    """删除用户注册、导入或当前工作区磁盘上的 Skill。内置 Skill 只能卸载，不能删除。"""

    if skill_id not in _skills:
        project_root = _project_root(project_slug)
        current = find_skill(skill_id, project_root)
        if current is None or project_root is None:
            raise HTTPException(status_code=404, detail="Skill not found")
        delete_external_skill(current, project_root=project_root)
        _job_service.pause_jobs_for_catalog_change(resource_type="skill", resource_id=skill_id)
        remove_skills_from_agents({skill_id})
        return

    if skill_id in _builtin_skills:
        raise HTTPException(status_code=409, detail="Built-in Skill cannot be deleted")

    skill = _skills[skill_id]
    _job_service.pause_jobs_for_catalog_change(resource_type="skill", resource_id=skill_id)
    remove_skills_from_agents({skill_id})
    delete_registered_skill_files(skill)
    _external_skill_overrides.pop(skill_id, None)
    delete_skill_override(skill_id)
    reload_skill_cache(_project_root(project_slug))


@router.post("/{skill_id}/enable", response_model=Skill)
async def set_skill_enabled(
    skill_id: str,
    data: SkillInstallRequest,
    project_slug: Annotated[str, Query(alias="projectSlug")] = "default",
) -> Skill:
    """启用或停用已安装的 Skill。"""

    if skill_id not in _skills:
        project = get_project(project_slug)
        external = (
            discover_external_skills(Path(str(project.get("primaryPath") or "")))
            if project
            else []
        )
        current = next((skill for skill in external if skill.id == skill_id), None)
        if current is None:
            raise HTTPException(status_code=404, detail="Skill not found")
        if current.status != "installed":
            raise HTTPException(status_code=409, detail="Skill is not installed")
        _upsert_external_override(skill_id, enabled=data.enabled)
        if not data.enabled:
            _job_service.pause_jobs_for_catalog_change(resource_type="skill", resource_id=skill_id)
        return _with_share_flag(current.model_copy(update={"enabled": data.enabled, "updated_at": _utc_now()}))

    current = _skills[skill_id]
    if current.status != "installed":
        raise HTTPException(status_code=409, detail="Skill is not installed")

    _upsert_external_override(skill_id, installed=True, enabled=data.enabled)
    reload_skill_cache(Path(str(get_project(project_slug).get("primaryPath") or "")) if get_project(project_slug) else None)
    if not data.enabled:
        _job_service.pause_jobs_for_catalog_change(resource_type="skill", resource_id=skill_id)
    return _with_share_flag(_skills[skill_id])


@router.post("/{skill_id}/share", response_model=Skill)
async def set_skill_shared(
    skill_id: str,
    data: SkillShareRequest,
    project_slug: Annotated[str, Query(alias="projectSlug")] = "default",
) -> Skill:
    """打开或关闭「所有 Agent 可用」。"""

    if _is_share_locked(skill_id):
        raise HTTPException(status_code=409, detail="系统说明书技能固定对所有 Agent 可用，不能关闭共享")

    if skill_id not in _skills:
        project = get_project(project_slug)
        external = (
            discover_external_skills(Path(str(project.get("primaryPath") or "")))
            if project
            else []
        )
        current = next((skill for skill in external if skill.id == skill_id), None)
        if current is None:
            raise HTTPException(status_code=404, detail="Skill not found")
        if current.status != "installed":
            raise HTTPException(status_code=409, detail="Skill is not installed")
        _upsert_external_override(skill_id, availableToAllAgents=data.available_to_all_agents)
        return current.model_copy(update={
            "available_to_all_agents": data.available_to_all_agents,
            "updated_at": _utc_now(),
        })

    current = _skills[skill_id]
    if current.status != "installed":
        raise HTTPException(status_code=409, detail="Skill is not installed")

    _upsert_external_override(skill_id, availableToAllAgents=data.available_to_all_agents)
    project = get_project(project_slug)
    reload_skill_cache(Path(str(project.get("primaryPath") or "")) if project else None)
    return _with_share_flag(_skills[skill_id])
