"""Skill 管理 HTTP 入口，统一委托文件 catalog/policy/reader。"""
from io import BytesIO
from pathlib import Path
import shutil
import tempfile
from typing import Annotated, Optional
from urllib.parse import quote

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse

# 兼容已有 Runtime 导入；实现本身位于 Skill 领域。
from src.skills.registry import (
    Skill, SkillRegisterRequest, SkillShareRequest, SkillInstallRequest,
    discover_external_skills, get_installed_skill, list_installed_enabled_skills,
    get_runtime_skill_catalog, list_skill_usage_details, resolve_installed_skills,
    resolve_runtime_skill_paths, register_user_skill, select_relevant_skills,
    build_skill_system_prompt, build_skill_system_prompt_from_skills,
    _skills, _builtin_skills, _load_user_skills, _get_loaded_skills,
    _normalized_skill_name, _with_share_flag, _is_share_locked, _hydrate_skill,
    discover_skill_packages,
)
from src.skills import SkillError
from src.skills.service import catalog_for, get_catalog_skill, list_catalog_skills, publish_skill, skill_from_candidate
from src.skills.policy import remove_skill_policy, set_skill_policy
from src.skills.packages import (
    MAX_SKILL_ARCHIVE_BYTES, _read_skill_md_from_zip, _extract_skill_package,
    _skill_export_archive, _skill_export_filename,
)
from src.skills.parser import parse_manual

router = APIRouter()
WorkspaceQuery = Annotated[str, Query(alias="workspaceId")]
ProjectQuery = Annotated[str, Query(alias="projectId")]


def _catalog(workspace_id="default", project_id=""):
    try:
        return catalog_for()
    except SkillError as exc:
        raise _http_error(exc) from exc


def _http_error(exc):
    status = {"skill_missing": 404, "skill_scope_denied": 403, "skill_ambiguous": 409,
              "skill_policy_conflict": 409, "skill_exists": 409, "skill_reserved_name": 409}.get(exc.code, 422)
    return HTTPException(status_code=status, detail=exc.as_dict())


def _get(ref, catalog, include_body=True):
    try:
        return get_catalog_skill(ref, catalog, include_body=include_body)
    except SkillError as exc:
        raise _http_error(exc) from exc


@router.get("", response_model=list[Skill])
async def list_skills(q: str = "", category: str = "", installed: Optional[bool] = None,
                      workspace_id: WorkspaceQuery = "default", project_id: ProjectQuery = ""):
    """目录只返回统一应用 Skill 根中的真实包，初始列表不携带正文。"""
    result = list_catalog_skills(_catalog(workspace_id, project_id))
    query = q.strip().lower()
    return [item for item in result if (not query or query in " ".join([item.name, item.description, item.trigger, *item.tags]).lower())
            and (not category or item.category == category)
            and (installed is None or (item.status == "installed") == installed)]


@router.get("/categories", response_model=list[str])
async def list_skill_categories(workspace_id: WorkspaceQuery = "default", project_id: ProjectQuery = ""):
    """分类使用相同上下文目录。"""
    return sorted({item.category for item in list_catalog_skills(_catalog(workspace_id, project_id))})


@router.get("/enabled", response_model=list[Skill])
async def list_enabled_skills(workspace_id: WorkspaceQuery = "default", project_id: ProjectQuery = ""):
    """选择器只给出可执行的精确 loadName，同层同名可显式消歧。"""
    return [item for item in list_catalog_skills(_catalog(workspace_id, project_id))
            if item.status == "installed" and item.enabled and item.resolution_state != "shadowed"]


@router.post("/register", response_model=Skill, status_code=201)
async def register_skill(data: SkillRegisterRequest):
    """原子发布到 Workspace 受管根，不进行数据库登记。"""
    return register_user_skill(data)


@router.post("/import", response_model=Skill, status_code=201)
async def import_skill_archive(file: UploadFile = File(...), workspace_id: WorkspaceQuery = "default"):
    """复用 ZIP 安全校验，在隐藏 staging 中验证后原子发布。"""
    if not (file.filename or "").lower().endswith(".zip"):
        raise HTTPException(status_code=422, detail="Only .zip skill archives are supported")
    raw = await file.read(MAX_SKILL_ARCHIVE_BYTES + 1)
    if len(raw) > MAX_SKILL_ARCHIVE_BYTES:
        raise HTTPException(status_code=413, detail="Skill archive is too large")
    skill_path, markdown, archive, members = _read_skill_md_from_zip(raw)
    try:
        parsed = parse_manual(markdown.encode(), Path(skill_path).parent.name or "imported")
        with tempfile.TemporaryDirectory(prefix="yinli-skill-import-") as stage:
            package_path, _ = _extract_skill_package(archive, members, skill_path, parsed.name, packages_dir=stage)
            return publish_skill(_catalog(workspace_id).context, parsed.name, markdown, source_directory=Path(package_path))
    except SkillError as exc:
        raise _http_error(exc) from exc
    finally:
        archive.close()


@router.get("/{skill_id}", response_model=Skill)
async def get_skill(skill_id: str, workspace_id: WorkspaceQuery = "default", project_id: ProjectQuery = ""):
    """详情读取统一应用根中的真实包，不要求数据库登记行。"""
    return _get(skill_id, _catalog(workspace_id, project_id))


@router.get("/{skill_id}/export")
async def export_skill(skill_id: str, workspace_id: WorkspaceQuery = "default", project_id: ProjectQuery = ""):
    """导出同一已解析包，丢失文件不使用数据库正文合成。"""
    skill = _get(skill_id, _catalog(workspace_id, project_id))
    raw = _skill_export_archive(skill)
    filename = quote(_skill_export_filename(skill))
    return StreamingResponse(BytesIO(raw), media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="{filename}"; filename*=UTF-8\'\'{filename}'})


def _update(skill_id, workspace_id, project_id, **changes):
    if skill_id == "yinli-catalog" and any(changes.get(key) is False for key in ("enabled", "installed", "availableToAllAgents")):
        raise HTTPException(status_code=409, detail="系统说明书技能固定启用并共享")
    catalog = _catalog(workspace_id, project_id)
    skill = _get(skill_id, catalog, include_body=False)
    if skill.status != "installed" and changes.get("installed") is not True:
        raise HTTPException(status_code=409, detail="Skill is not installed")
    if skill.id == "yinli-catalog" and any(changes.get(key) is False for key in ("enabled", "installed", "availableToAllAgents")):
        raise HTTPException(status_code=409, detail="系统说明书技能固定启用并共享")
    try:
        set_skill_policy(catalog.context.policy_path, skill.id, **changes)
    except SkillError as exc:
        raise _http_error(exc) from exc
    return _get(skill.id, _catalog(workspace_id, project_id), include_body=False)


@router.post("/{skill_id}/install", response_model=Skill)
async def install_skill(skill_id: str, data: SkillInstallRequest, workspace_id: WorkspaceQuery = "default", project_id: ProjectQuery = ""):
    """安装状态独立保存。"""
    return _update(skill_id, workspace_id, project_id, installed=True, enabled=data.enabled)


@router.post("/{skill_id}/uninstall", response_model=Skill)
async def uninstall_skill(skill_id: str, workspace_id: WorkspaceQuery = "default", project_id: ProjectQuery = ""):
    """保留磁盘包以便重新安装。"""
    return _update(skill_id, workspace_id, project_id, installed=False, enabled=False)


@router.post("/{skill_id}/enable", response_model=Skill)
async def set_skill_enabled(skill_id: str, data: SkillInstallRequest, workspace_id: WorkspaceQuery = "default", project_id: ProjectQuery = ""):
    """只修改稳定 ID 的启用策略。"""
    return _update(skill_id, workspace_id, project_id, enabled=data.enabled)


@router.post("/{skill_id}/share", response_model=Skill)
async def set_skill_shared(skill_id: str, data: SkillShareRequest, workspace_id: WorkspaceQuery = "default", project_id: ProjectQuery = ""):
    """共享不改变 Job 显式 required 集合。"""
    return _update(skill_id, workspace_id, project_id, availableToAllAgents=data.available_to_all_agents)


@router.delete("/{skill_id}", status_code=204)
async def delete_skill(skill_id: str, workspace_id: WorkspaceQuery = "default", project_id: ProjectQuery = "") -> None:
    """仅允许删除当前 Workspace 的受管包。"""
    catalog = _catalog(workspace_id, project_id)
    candidate = next(
        (item for item in catalog.candidates if skill_id in (item.id, item.load_name)),
        None,
    )
    skill = skill_from_candidate(candidate, catalog, include_body=False) if candidate else _get(
        skill_id,
        catalog,
        include_body=False,
    )
    if not skill.managed:
        raise HTTPException(status_code=409, detail="只能删除应用 Skill 目录中的普通受管包")
    from src.server.routes.agents import remove_skills_from_agents
    remove_skills_from_agents({skill.id})
    shutil.rmtree(skill.resource_path)
    remove_skill_policy(catalog.context.policy_path, skill.id, relative_path=candidate.relative_path if candidate else "")
