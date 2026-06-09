"""
Skills 管理路由

提供 Skill 浏览、安装状态管理和自定义 Skill 注册。
MVP 阶段使用内存 registry + JSON 文件持久化用户 Skill。
"""

import json
import os
import re
import shutil
import zipfile
from io import BytesIO
from datetime import datetime, timezone
from typing import Literal, Optional

import yaml
from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from src.server.routes.agents import remove_skills_from_agents
from src.storage.database import (
    list_skills as db_list_skills,
    list_json_records,
    migrate_json_list_file,
    replace_skills,
)

router = APIRouter()

SkillInstallStatus = Literal["available", "installed", "error"]

DATA_DIR = "data"
USER_SKILLS_REGISTRY_FILE = os.path.join(DATA_DIR, "skills.json")
SKILLS_NAMESPACE = "skills"
SKILL_PACKAGES_DIR = os.path.join(DATA_DIR, "skill_packages")
MAX_SKILL_ARCHIVE_BYTES = 10 * 1024 * 1024
MAX_SKILL_PACKAGE_FILES = 200


class Skill(BaseModel):
    """Skill 描述"""

    id: str
    name: str
    description: str
    category: str
    trigger: str = ""
    content: str = ""
    tags: list[str] = Field(default_factory=list)
    resource_path: str = Field(default="", alias="resourcePath")
    resources: list[str] = Field(default_factory=list)
    status: SkillInstallStatus = "available"
    enabled: bool = False
    installed_at: Optional[str] = Field(default=None, alias="installedAt")
    updated_at: str = Field(alias="updatedAt")

    model_config = {"populate_by_name": True}


class SkillInstallRequest(BaseModel):
    """安装/配置 Skill 请求"""

    enabled: bool = True


class SkillRegisterRequest(BaseModel):
    """注册自定义 Skill 请求"""

    name: str = Field(..., min_length=1, max_length=80)
    description: str = Field(default="", max_length=300)
    category: str = Field(default="自定义", max_length=40)
    trigger: str = Field(default="", max_length=200)
    content: str = Field(default="", max_length=12000)
    tags: list[str] = Field(default_factory=list)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "custom-skill"


def _unique_skill_id(name: str) -> str:
    base = f"custom-{_slugify(name)}"
    if base not in _skills:
        return base

    index = 2
    while f"{base}-{index}" in _skills:
        index += 1
    return f"{base}-{index}"


def _skill(
    skill_id: str,
    name: str,
    description: str,
    category: str,
    trigger: str,
    content: str,
    tags: list[str],
) -> Skill:
    return Skill.model_validate(
        {
            "id": skill_id,
            "name": name,
            "description": description,
            "category": category,
            "trigger": trigger,
            "content": content,
            "tags": tags,
            "resourcePath": "",
            "resources": [],
            "status": "available",
            "enabled": False,
            "installedAt": None,
            "updatedAt": _utc_now(),
        }
    )


def _split_frontmatter(markdown: str) -> tuple[dict, str]:
    if not markdown.startswith("---"):
        return {}, markdown.strip()

    parts = markdown.split("---", 2)
    if len(parts) < 3:
        return {}, markdown.strip()

    try:
        metadata = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid SKILL.md frontmatter: {exc}") from exc

    if not isinstance(metadata, dict):
        raise HTTPException(status_code=422, detail="SKILL.md frontmatter must be a YAML object")

    return metadata, parts[2].strip()


def _safe_archive_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    members: list[zipfile.ZipInfo] = []
    for info in archive.infolist():
        if info.is_dir():
            continue
        if info.filename.startswith("__MACOSX/"):
            continue
        normalized = os.path.normpath(info.filename)
        if normalized.startswith("../") or os.path.isabs(normalized):
            raise HTTPException(status_code=422, detail="Zip archive contains unsafe paths")
        members.append(info)

    if len(members) > MAX_SKILL_PACKAGE_FILES:
        raise HTTPException(status_code=413, detail="Skill archive contains too many files")
    return members


def _read_skill_md_from_zip(raw: bytes) -> tuple[str, str, zipfile.ZipFile, list[zipfile.ZipInfo]]:
    try:
        archive = zipfile.ZipFile(BytesIO(raw))
        try:
            members = _safe_archive_members(archive)
            skill_paths = [
                info.filename
                for info in members
                if os.path.basename(info.filename) == "SKILL.md"
            ]
            if not skill_paths:
                raise HTTPException(status_code=422, detail="Zip archive must contain a SKILL.md file")

            skill_path = sorted(skill_paths, key=lambda item: item.count("/"))[0]
            info = archive.getinfo(skill_path)
            if info.file_size > MAX_SKILL_ARCHIVE_BYTES:
                raise HTTPException(status_code=413, detail="SKILL.md is too large")

            return skill_path, archive.read(skill_path).decode("utf-8"), archive, members
        except Exception:
            archive.close()
            raise
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=422, detail="Invalid zip archive") from exc
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=422, detail="SKILL.md must be UTF-8 encoded") from exc


def _package_relative_path(skill_path: str, filename: str) -> str:
    root = os.path.dirname(skill_path)
    if root and filename.startswith(root + "/"):
        return filename[len(root) + 1:]
    return filename


def _extract_skill_package(
    archive: zipfile.ZipFile,
    members: list[zipfile.ZipInfo],
    skill_path: str,
    skill_id: str,
) -> tuple[str, list[str]]:
    package_dir = os.path.join(SKILL_PACKAGES_DIR, skill_id)
    if os.path.exists(package_dir):
        shutil.rmtree(package_dir)
    os.makedirs(package_dir, exist_ok=True)

    resources: list[str] = []
    for info in members:
        relative_path = _package_relative_path(skill_path, info.filename)
        if relative_path == "." or relative_path.startswith("../"):
            continue

        target_path = os.path.join(package_dir, relative_path)
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        with archive.open(info) as source, open(target_path, "wb") as target:
            shutil.copyfileobj(source, target)
        resources.append(relative_path)

    return package_dir, sorted(resources)


def _skill_from_markdown(
    skill_path: str,
    markdown: str,
    resource_path: str = "",
    resources: Optional[list[str]] = None,
) -> Skill:
    metadata, body = _split_frontmatter(markdown)
    name = str(metadata.get("name") or os.path.basename(os.path.dirname(skill_path)) or "Imported Skill")
    description = str(metadata.get("description") or "")
    short_description = ""
    raw_metadata = metadata.get("metadata")
    if isinstance(raw_metadata, dict):
        short_description = str(raw_metadata.get("short-description") or "")

    skill_id = _unique_skill_id(name)
    now = _utc_now()
    return Skill.model_validate(
        {
            "id": skill_id,
            "name": name,
            "description": description or short_description or f"从压缩包导入的 Skill: {name}",
            "category": "导入",
            "trigger": description,
            "content": markdown.strip() or body,
            "tags": ["imported", "zip"],
            "resourcePath": resource_path,
            "resources": resources or [],
            "status": "installed",
            "enabled": True,
            "installedAt": now,
            "updatedAt": now,
        }
    )


_builtin_skills: dict[str, Skill] = {
    "code-review": _skill(
        "code-review",
        "Code Review",
        "审查代码变更，关注正确性、可维护性、测试覆盖和潜在风险。",
        "开发",
        "当用户要求审查代码、PR、diff 或定位潜在缺陷时触发。",
        "以代码审查视角输出问题清单，优先列出 bug、回归风险和缺失测试，并给出文件/行号依据。",
        ["code", "review", "quality"],
    ),
    "web-research": _skill(
        "web-research",
        "Web Research",
        "规划并执行网络资料检索，整理可信来源、结论和引用。",
        "研究",
        "当任务需要最新资料、外部事实核验或资料汇总时触发。",
        "优先使用权威来源，区分事实和推断，输出简洁结论与来源链接。",
        ["research", "web", "sources"],
    ),
    "data-analysis": _skill(
        "data-analysis",
        "Data Analysis",
        "分析 CSV、表格或结构化数据，生成摘要、洞察和可视化建议。",
        "数据",
        "当用户提供数据文件、统计问题或分析目标时触发。",
        "先确认字段和数据质量，再进行聚合、比较、趋势和异常分析。",
        ["data", "analysis", "csv"],
    ),
    "document-writing": _skill(
        "document-writing",
        "Document Writing",
        "生成或润色需求文档、设计文档、说明书和报告。",
        "文档",
        "当用户要求编写、整理、润色或重构文档时触发。",
        "保持结构清晰、措辞准确，优先补齐目标、范围、约束和验收标准。",
        ["docs", "writing", "report"],
    ),
    "test-planning": _skill(
        "test-planning",
        "Test Planning",
        "设计测试策略、测试用例和风险覆盖矩阵。",
        "质量",
        "当任务涉及测试计划、验收标准或质量保障时触发。",
        "覆盖核心路径、边界条件、错误状态和回归风险，并给出可执行测试清单。",
        ["test", "qa", "coverage"],
    ),
    "release-notes": _skill(
        "release-notes",
        "Release Notes",
        "根据变更内容生成面向用户或开发者的发布说明。",
        "交付",
        "当用户要求总结变更、写 changelog 或 release notes 时触发。",
        "按新增、修复、变更、已知问题组织，避免夸大未完成能力。",
        ["release", "changelog", "summary"],
    ),
}

_skills: dict[str, Skill] = dict(_builtin_skills)


def _load_user_skills() -> None:
    migrate_json_list_file(SKILLS_NAMESPACE, USER_SKILLS_REGISTRY_FILE)
    records = db_list_skills()
    if not records:
        legacy = list_json_records(SKILLS_NAMESPACE)
        replace_skills({str(item["id"]): item for item in legacy if item.get("id")})
        records = db_list_skills()
    for item in records:
        skill = Skill.model_validate(item)
        _skills[skill.id] = skill


def _save_user_skills() -> None:
    replace_skills({
        skill_id: skill.model_dump(by_alias=True)
        for skill_id, skill in _skills.items()
        if skill_id not in _builtin_skills
    })


def _persist_if_user_skill(skill_id: str) -> None:
    if skill_id not in _builtin_skills:
        _save_user_skills()


def build_skill_system_prompt(skill_ids: list[str]) -> str:
    """构建注入 Agent system prompt 的 Skill 操作手册上下文。"""
    return build_skill_system_prompt_from_skills(_get_loaded_skills(skill_ids))


def build_skill_system_prompt_from_skills(selected_skills: list[Skill]) -> str:
    """根据指定 Skill 列表构建注入 Agent system prompt 的上下文。"""
    if not selected_skills:
        return ""

    sections = [
        "## Loaded Skills",
        "The following Skills are already loaded into this Agent. When the user asks to use a Skill, or when the task matches a Skill trigger, apply the loaded Skill instructions directly.",
        "Do not search the filesystem to discover Skill instructions. Use only the Skill manuals and resource paths listed below.",
        "If a Skill has scripts, references, or assets, use the listed package resource path for that Skill instead of scanning unrelated directories.",
    ]

    for index, skill in enumerate(selected_skills, start=1):
        resource_path = os.path.abspath(skill.resource_path) if skill.resource_path else ""
        resources = "\n".join(f"  - {item}" for item in skill.resources[:80])
        sections.extend([
            f"### Skill {index}: {skill.name}",
            f"- id: {skill.id}",
            f"- description: {skill.description}",
            f"- trigger: {skill.trigger or 'Use when the user explicitly asks for this Skill or the task matches the manual.'}",
            f"- package_resource_path: {resource_path or 'none'}",
            "- package_resources:",
            resources or "  - none",
            "",
            "#### SKILL.md",
            skill.content.strip() or "(empty)",
        ])

    return "\n".join(sections)


def list_skill_usage_details(skills: list[Skill]) -> list[dict[str, object]]:
    """返回本次实际使用的 Skill 详情，用于前端反馈。"""
    return [
        {
            "id": skill.id,
            "name": skill.name,
            "description": skill.description,
            "trigger": skill.trigger,
            "content": skill.content,
            "resourcePath": os.path.abspath(skill.resource_path) if skill.resource_path else "",
            "resources": skill.resources,
        }
        for skill in skills
    ]


def select_relevant_skills(skill_ids: list[str], message: str) -> list[Skill]:
    """根据用户消息从 Agent 绑定的 Skills 中选择本次要使用的 Skill。"""
    loaded_skills = _get_loaded_skills(skill_ids)
    if not loaded_skills:
        return []

    normalized_message = message.strip().lower()
    explicit_skill_request = any(
        marker in normalized_message
        for marker in ("skill", "skills", "技能")
    )

    scored: list[tuple[int, Skill]] = []
    for skill in loaded_skills:
        score = 0
        searchable_fields = [
            skill.id,
            skill.name,
            skill.description,
            skill.trigger,
            *skill.tags,
        ]
        for field in searchable_fields:
            value = field.strip().lower()
            if value and value in normalized_message:
                score += 4

        for token in _keyword_tokens(skill.name, skill.description, skill.trigger, *skill.tags):
            if token in normalized_message:
                score += 1

        if score > 0:
            scored.append((score, skill))

    if scored:
        scored.sort(key=lambda item: item[0], reverse=True)
        top_score = scored[0][0]
        return [
            skill
            for score, skill in scored
            if score == top_score or score >= 4
        ][:3]

    if explicit_skill_request and len(loaded_skills) == 1:
        return loaded_skills

    return []


def _get_loaded_skills(skill_ids: list[str]) -> list[Skill]:
    return [
        _skills[skill_id]
        for skill_id in skill_ids
        if skill_id in _skills
        and _skills[skill_id].status == "installed"
        and _skills[skill_id].enabled
    ]


def _keyword_tokens(*values: str) -> set[str]:
    tokens: set[str] = set()
    for value in values:
        for token in re.findall(r"[a-z0-9\u4e00-\u9fff]{2,}", value.lower()):
            tokens.add(token)
    return tokens


_load_user_skills()


@router.get("", response_model=list[Skill])
async def list_skills(
    q: str = "",
    category: str = "",
    installed: Optional[bool] = None,
) -> list[Skill]:
    """获取 Skill 列表，支持搜索、分类和安装状态筛选。"""

    query = q.strip().lower()
    result = list(_skills.values())

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

    return result


@router.get("/categories", response_model=list[str])
async def list_skill_categories() -> list[str]:
    """获取 Skill 分类列表。"""

    return sorted({skill.category for skill in _skills.values()})


@router.get("/enabled", response_model=list[Skill])
async def list_enabled_skills() -> list[Skill]:
    """获取已安装并启用的 Skills，用于 Agent 配置选择。"""

    return [
        skill
        for skill in _skills.values()
        if skill.status == "installed" and skill.enabled
    ]


@router.post("/register", response_model=Skill, status_code=201)
async def register_skill(data: SkillRegisterRequest) -> Skill:
    """注册自定义 Skill 并保存到本地 registry。"""

    skill_id = _unique_skill_id(data.name)
    now = _utc_now()
    skill = Skill.model_validate(
        {
            "id": skill_id,
            "name": data.name,
            "description": data.description or f"用户注册的 Skill: {data.name}",
            "category": data.category or "自定义",
            "trigger": data.trigger,
            "content": data.content,
            "tags": data.tags[:8],
            "status": "installed",
            "enabled": True,
            "installedAt": now,
            "updatedAt": now,
        }
    )
    _skills[skill.id] = skill
    _save_user_skills()
    return skill


@router.post("/import", response_model=Skill, status_code=201)
async def import_skill_archive(file: UploadFile = File(...)) -> Skill:
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
        resource_path, resources = _extract_skill_package(
            archive,
            members,
            skill_path,
            draft_skill.id,
        )
        skill = _skill_from_markdown(skill_path, markdown, resource_path, resources)
        _skills[skill.id] = skill
        _save_user_skills()
        return skill
    finally:
        archive.close()


@router.get("/{skill_id}", response_model=Skill)
async def get_skill(skill_id: str) -> Skill:
    """获取单个 Skill 详情。"""

    if skill_id not in _skills:
        raise HTTPException(status_code=404, detail="Skill not found")
    return _skills[skill_id]


@router.post("/{skill_id}/install", response_model=Skill)
async def install_skill(skill_id: str, data: SkillInstallRequest) -> Skill:
    """标记 Skill 为已安装，并设置启用状态。"""

    if skill_id not in _skills:
        raise HTTPException(status_code=404, detail="Skill not found")

    current = _skills[skill_id]
    skill = current.model_copy(
        update={
            "status": "installed",
            "enabled": data.enabled,
            "installed_at": current.installed_at or _utc_now(),
            "updated_at": _utc_now(),
        }
    )
    _skills[skill_id] = skill
    _persist_if_user_skill(skill_id)
    return skill


@router.post("/{skill_id}/uninstall", response_model=Skill)
async def uninstall_skill(skill_id: str) -> Skill:
    """卸载 Skill。"""

    if skill_id not in _skills:
        raise HTTPException(status_code=404, detail="Skill not found")

    skill = _skills[skill_id].model_copy(
        update={
            "status": "available",
            "enabled": False,
            "installed_at": None,
            "updated_at": _utc_now(),
        }
    )
    _skills[skill_id] = skill
    _persist_if_user_skill(skill_id)
    return skill


@router.delete("/{skill_id}", status_code=204)
async def delete_skill(skill_id: str) -> None:
    """删除用户注册或导入的 Skill。内置 Skill 只能卸载，不能删除。"""

    if skill_id not in _skills:
        raise HTTPException(status_code=404, detail="Skill not found")
    if skill_id in _builtin_skills:
        raise HTTPException(status_code=409, detail="Built-in Skill cannot be deleted")

    skill = _skills.pop(skill_id)
    remove_skills_from_agents({skill_id})
    if skill.resource_path and os.path.exists(skill.resource_path):
        shutil.rmtree(skill.resource_path)
    _save_user_skills()


@router.post("/{skill_id}/enable", response_model=Skill)
async def set_skill_enabled(skill_id: str, data: SkillInstallRequest) -> Skill:
    """启用或停用已安装的 Skill。"""

    if skill_id not in _skills:
        raise HTTPException(status_code=404, detail="Skill not found")

    current = _skills[skill_id]
    if current.status != "installed":
        raise HTTPException(status_code=409, detail="Skill is not installed")

    skill = current.model_copy(
        update={
            "enabled": data.enabled,
            "updated_at": _utc_now(),
        }
    )
    _skills[skill_id] = skill
    _persist_if_user_skill(skill_id)
    return skill
