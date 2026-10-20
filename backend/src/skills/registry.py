"""Skill 目录、注册、选择和运行时路径。

HTTP 路由留在 src.server.routes.skills。这里不依赖 FastAPI 路由对象。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

import yaml
from fastapi import HTTPException
from pydantic import BaseModel, Field

from src.catalog.skills import (
    ALWAYS_INJECTED_SKILL_IDS,
    SYSTEM_SKILL_IDS,
    DiscoveredSkill,
    discover_skill_packages,
    read_package_markdown,
)
from src.config.runtime_paths import (
    data_root,
    ensure_tenant_directories,
    skill_packages_root,
    tenant_root,
    user_home_root as runtime_user_home_root,
)
from src.server.routes.skill_packages import (
    _cleanup_skill_package_artifacts,
    _extract_skill_package as _extract_skill_package_impl,
    _filename_slug,
    _read_skill_md_from_zip,
    _skill_export_archive,
    _skill_export_filename,
    _skill_package_dir as _skill_package_dir_impl,
    _skill_package_warnings,
    _split_frontmatter,
    _write_skill_package as _write_skill_package_impl,
)
from src.storage.database import (
    get_project,
    delete_json_record,
    list_json_records,
    list_skills as db_list_skills,
    migrate_json_list_file,
    replace_skills,
    upsert_json_record,
)

logger = logging.getLogger(__name__)


def _packages_dir() -> str:
    """当前租户的 Skill 包根。"""

    return str(skill_packages_root())


def _discover_packages(*args, **kwargs):
    """读取路由模块上的发现函数，测试会替换那个名字。"""

    from src.server.routes.skills import discover_skill_packages

    return discover_skill_packages(*args, **kwargs)


SKILLS_NAMESPACE = "skills"
EXTERNAL_SKILL_OVERRIDES_NAMESPACE = "external_skill_overrides"
# {skill_id: {"enabled"?: bool, "availableToAllAgents"?: bool}}
_external_skill_overrides: dict[str, dict[str, bool]] = {}


SkillInstallStatus = Literal["available", "installed", "error"]


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
    warnings: list[str] = Field(default_factory=list)
    status: SkillInstallStatus = "available"
    enabled: bool = False
    available_to_all_agents: bool = Field(default=True, alias="availableToAllAgents")
    installed_at: Optional[str] = Field(default=None, alias="installedAt")
    updated_at: str = Field(alias="updatedAt")
    workspace_id: str = Field(default="default", alias="workspaceId")
    source_kind: str = Field(default="native", alias="sourceKind")
    source_scope: str = Field(default="system", alias="sourceScope")
    source_path: str = Field(default="", alias="sourcePath")
    content_hash: str = Field(default="", alias="contentHash")

    model_config = {"populate_by_name": True}


class SkillInstallRequest(BaseModel):
    """安装/配置 Skill 请求"""

    enabled: bool = True


class SkillShareRequest(BaseModel):
    """切换「所有 Agent 可用」请求。"""

    available_to_all_agents: bool = Field(alias="availableToAllAgents")

    model_config = {"populate_by_name": True}


class SkillRegisterRequest(BaseModel):
    """注册自定义 Skill 请求"""

    name: str = Field(..., min_length=1, max_length=80)
    description: str = Field(default="", max_length=300)
    category: str = Field(default="自定义", max_length=40)
    trigger: str = Field(default="", max_length=200)
    content: str = Field(default="", max_length=12000)
    tags: list[str] = Field(default_factory=list)
    workspace_id: str = Field(default="default", alias="workspaceId")

    model_config = {"populate_by_name": True}


def _skill_package_dir(skill_name: str) -> str:
    """使用当前可替换的包根目录解析 Skill 路径。"""

    return _skill_package_dir_impl(skill_name, packages_dir=_packages_dir())


def _write_skill_package(skill_name: str, markdown: str) -> tuple[str, list[str]]:
    """使用当前包根写入标准 Skill 包。"""

    return _write_skill_package_impl(
        skill_name,
        markdown,
        packages_dir=_packages_dir(),
    )


def _extract_skill_package(
    archive,
    members,
    skill_path: str,
    skill_name: str,
) -> tuple[str, list[str]]:
    """使用当前包根解压经过校验的 Skill 包。"""

    return _extract_skill_package_impl(
        archive,
        members,
        skill_path,
        skill_name,
        packages_dir=_packages_dir(),
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "custom-skill"


def _normalized_skill_name(value: str) -> str:
    """归一化 Skill 名称，用于重复判断。"""

    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", value.lower())


def _find_skill_by_name(name: str) -> Skill | None:
    normalized = _normalized_skill_name(name)
    if not normalized:
        return None
    for skill in _skills.values():
        if _normalized_skill_name(skill.name) == normalized:
            return skill
    return None


def _ensure_skill_name_available(name: str) -> None:
    duplicate = _find_skill_by_name(name)
    if duplicate:
        raise HTTPException(
            status_code=409,
            detail=f"Skill 名称已存在：{duplicate.name}",
        )


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


def _skill_markdown_from_registration(data: SkillRegisterRequest) -> str:
    """把注册请求转换为标准 SKILL.md 内容。"""

    body = (data.content or "").strip()
    if body.startswith("---"):
        return body
    if not body:
        body = f"# {data.name}\n\n请在这里补充这个 Skill 的具体执行步骤、输入约束和输出要求。"
    metadata = {
        "name": data.name,
        "description": data.description or f"用户注册的 Skill: {data.name}",
        "metadata": {
            "short-description": data.description or f"用户注册的 Skill: {data.name}",
        },
        "category": data.category or "自定义",
        "trigger": data.trigger or "",
        "tags": data.tags[:8],
    }
    frontmatter = yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False).strip()
    return f"---\n{frontmatter}\n---\n\n{body.strip()}\n"


def _clip_text(value: Optional[str], limit: int) -> str:
    """把字段裁到注册请求允许的最大长度，超长时记录 warning。"""

    text = (value or "").strip()
    if len(text) <= limit:
        return text
    logger.warning("Skill 字段超过 %s 字，已截断: %r", limit, text[:40])
    return text[:limit].rstrip()


def _ensure_skill_package(skill: Skill) -> Skill:
    """为历史用户 Skill 补齐标准 Skill 包目录。"""

    expected_package_dir = _skill_package_dir(skill.name)
    if (
        skill.resource_path == expected_package_dir
        and os.path.isfile(os.path.join(expected_package_dir, "SKILL.md"))
    ):
        resources = _cleanup_skill_package_artifacts(expected_package_dir)
        if resources == skill.resources:
            return skill
        return skill.model_copy(update={"resources": resources, "updated_at": _utc_now()})

    markdown = (skill.content or "").strip()
    if not markdown.startswith("---"):
        # 历史 / 自进化 Skill 的字段可能超过注册请求上限（例如 trigger > 200 字），
        # 这里按上限截断而不是抛 ValidationError，否则启动导入会整体崩溃。
        markdown = _skill_markdown_from_registration(
            SkillRegisterRequest(
                name=_clip_text(skill.name, 80) or skill.id,
                description=_clip_text(skill.description, 300),
                category=_clip_text(skill.category, 40) or "自定义",
                trigger=_clip_text(skill.trigger, 200),
                content=_clip_text(markdown, 12000),
                tags=skill.tags,
            )
        )
    resource_path, resources = _write_skill_package(skill.name, markdown)
    return skill.model_copy(
        update={
            "content": markdown,
            "resource_path": resource_path,
            "resources": resources,
            "updated_at": _utc_now(),
        }
    )


def _skill_from_markdown(
    skill_path: str,
    markdown: str,
    resource_path: str = "",
    resources: Optional[list[str]] = None,
) -> Skill:
    metadata, body = _split_frontmatter(markdown)
    name = str(metadata.get("name") or "").strip()
    description = str(metadata.get("description") or "").strip()
    if not name or not description:
        raise HTTPException(
            status_code=422,
            detail="SKILL.md frontmatter must contain non-empty name and description fields",
        )

    warnings = _skill_package_warnings(
        name=name,
        skill_path=skill_path,
        resources=resources or [],
    )

    skill_id = _unique_skill_id(name)
    now = _utc_now()
    return Skill.model_validate(
        {
            "id": skill_id,
            "name": name,
            "description": description,
            "category": "导入",
            "trigger": description,
            "content": markdown.strip() or body,
            "tags": ["imported", "zip"],
            "resourcePath": resource_path,
            "resources": resources or [],
            "warnings": warnings,
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


def _is_share_locked(skill_id: str) -> bool:
    """yinli-catalog 对合格 Chat 锁定全体可用。"""

    return skill_id in ALWAYS_INJECTED_SKILL_IDS


def _override_record(skill_id: str) -> dict[str, bool]:
    return dict(_external_skill_overrides.get(skill_id) or {})


def _upsert_external_override(skill_id: str, **fields: bool) -> dict[str, bool]:
    """写入磁盘发现包与系统包的 enabled / 共享覆盖，不丢另一字段。"""

    current = _override_record(skill_id)
    current.update(fields)
    _external_skill_overrides[skill_id] = current
    upsert_json_record(
        EXTERNAL_SKILL_OVERRIDES_NAMESPACE,
        skill_id,
        {"id": skill_id, **current},
    )
    return current


def _resolved_share_flag(skill: Skill) -> bool:
    if _is_share_locked(skill.id):
        return True
    record = _override_record(skill.id)
    if "availableToAllAgents" in record:
        return bool(record["availableToAllAgents"])
    return bool(skill.available_to_all_agents)


def _with_share_flag(skill: Skill) -> Skill:
    """对外 JSON / Runtime 统一叠共享开关，yinli-catalog 恒为 true。"""

    flag = _resolved_share_flag(skill)
    if flag == skill.available_to_all_agents:
        return skill
    return skill.model_copy(update={"available_to_all_agents": flag})


def refresh_skill_package(skill: Skill) -> Skill:
    """按磁盘上的 SKILL.md 刷新详情。列表和运行时都不停在进程启动时的副本。"""

    if not skill.resource_path:
        return skill
    package = Path(skill.resource_path)
    markdown = read_package_markdown(package)
    if not markdown.strip():
        return skill
    metadata, _body = _split_frontmatter(markdown)
    name = str(metadata.get("name") or skill.name).strip() or skill.name
    description = str(metadata.get("description") or skill.description).strip() or skill.description
    trigger = str(metadata.get("trigger") or description or skill.trigger).strip()
    resources = skill.resources
    if package.is_dir():
        resources = sorted(
            str(path.relative_to(package))
            for path in package.rglob("*")
            if path.is_file()
        )
    updated_at = skill.updated_at
    skill_md = package / "SKILL.md"
    try:
        updated_at = datetime.fromtimestamp(skill_md.stat().st_mtime, timezone.utc).isoformat()
    except OSError:
        updated_at = skill.updated_at
    return skill.model_copy(update={
        "name": name,
        "description": description,
        "trigger": trigger,
        "content": markdown.strip(),
        "resources": resources,
        "content_hash": hashlib.sha256(markdown.encode("utf-8")).hexdigest()[:16],
        "updated_at": updated_at,
    })


def _hydrate_skill(skill: Skill) -> Skill:
    """API / 运行时正文从 SKILL.md 读取，不信任 sqlite content。"""

    return refresh_skill_package(skill)


def _skill_from_discovered(item: DiscoveredSkill) -> Skill:
    return Skill.model_validate({
        "id": item.id,
        "name": item.name,
        "description": item.description,
        "category": "系统" if item.source_scope == "system" else "外部导入",
        "trigger": item.trigger,
        "content": item.markdown,
        "tags": [item.source_scope, item.source_kind],
        "resourcePath": item.resource_path,
        "resources": item.resources,
        "warnings": [],
        "status": "installed",
        "enabled": item.enabled,
        "updatedAt": _utc_now(),
        "workspaceId": "default",
        "sourceKind": item.source_kind,
        "sourceScope": item.source_scope,
        "sourcePath": item.source_path,
        "contentHash": item.content_hash,
    })


def _load_system_packages() -> None:
    """seed 系统包并登记为不可删除的内置 Skill。"""

    from src.catalog.skill_seed import seed_system_skills
    from src.catalog.skills import list_system_skill_packages

    seed_system_skills()
    for item in list_system_skill_packages():
        skill = _skill_from_discovered(item).model_copy(update={
            "enabled": True,
            "status": "installed",
            "category": "系统",
            "available_to_all_agents": True,
        })
        _builtin_skills[skill.id] = skill
        _skills[skill.id] = _with_share_flag(skill)


def _load_user_skills() -> None:
    ensure_tenant_directories()
    migrate_json_list_file(SKILLS_NAMESPACE, str(data_root() / "skills.json"))
    records = db_list_skills()
    if not records:
        legacy = list_json_records(SKILLS_NAMESPACE)
        replace_skills({str(item["id"]): item for item in legacy if item.get("id")})
        records = db_list_skills()
    _load_external_skill_overrides()
    for item in records:
        try:
            skill = Skill.model_validate(item)
        except Exception:
            logger.exception("跳过无法解析的 Skill 记录: %s", item.get("id"))
            continue
        if skill.id not in _builtin_skills:
            # 单个历史 Skill 补包失败不能拖垮整个后端启动，退回原始记录继续加载。
            try:
                skill = _ensure_skill_package(skill)
                skill = _hydrate_skill(skill)
            except Exception:
                logger.exception("Skill %s 补齐标准包失败，按原始记录加载", skill.id)
        _skills[skill.id] = _with_share_flag(skill)
    _load_system_packages()
    _save_user_skills()


def discover_external_skills(
    project_root: Path,
    *,
    user_data_root: Path | None = None,
    user_home_root: Path | None = None,
    workspace_id: str = "default",
) -> list[Skill]:
    """发现标准 Agent/Claude Skill 包；项目级默认启用，用户级默认关闭。

    user_home_root 为 None（hosted）时跳过 HOME scope（§12.7）。
    """

    discovered: list[Skill] = []
    for item in _discover_packages(
        Path(project_root),
        user_data_root=Path(user_data_root) if user_data_root is not None else tenant_root(),
        user_home_root=Path(user_home_root) if user_home_root is not None else runtime_user_home_root(),
        workspace_id=workspace_id,
    ):
        skill = _skill_from_discovered(item)
        record = _override_record(skill.id)
        installed, enabled = _external_install_state(item, record)
        share = True if _is_share_locked(skill.id) else (
            bool(record["availableToAllAgents"]) if "availableToAllAgents" in record else True
        )
        discovered.append(refresh_skill_package(skill.model_copy(update={
            "status": "installed" if installed else "available",
            "enabled": enabled,
            "available_to_all_agents": share,
        })))
    return discovered


def _external_install_state(item: DiscoveredSkill, record: dict[str, bool]) -> tuple[bool, bool]:
    """磁盘包的安装状态。用户目录默认未安装；显式覆盖优先于这个默认值。"""

    if "installed" in record:
        installed = bool(record["installed"])
    elif "enabled" in record:
        installed = True
    else:
        installed = item.source_scope != "user"
    if not installed:
        return False, False
    enabled = bool(record["enabled"]) if "enabled" in record else item.enabled
    return True, enabled


def get_runtime_skill_catalog(project_root: Path) -> list[Skill]:
    """返回当前 Project 的原生和外部 Skill catalog。"""

    return [
        *[refresh_skill_package(skill) for skill in _skills.values()],
        *discover_external_skills(project_root),
    ]


def find_skill(
    skill_id: str,
    project_root: Path | None,
    *,
    workspace_id: str = "default",
) -> Skill | None:
    """按 ID 查找已登记 Skill，或当前 Project 磁盘上的 Skill 包。"""

    if skill_id in _skills:
        skill = _skills[skill_id]
        if skill.id in _builtin_skills or skill.workspace_id == workspace_id:
            return _with_share_flag(refresh_skill_package(skill))
    if project_root is None:
        return None
    for skill in discover_external_skills(project_root, workspace_id=workspace_id):
        if skill.id == skill_id:
            return _with_share_flag(skill)
    return None


def install_external_skill(skill: Skill, *, enabled: bool = True) -> Skill:
    """把磁盘上的 Skill 包标成已安装，不把它抄进用户注册表。"""

    _upsert_external_override(skill.id, installed=True, enabled=enabled)
    now = _utc_now()
    return skill.model_copy(update={
        "status": "installed",
        "enabled": enabled,
        "installed_at": skill.installed_at or now,
        "updated_at": now,
    })


def uninstall_external_skill(skill: Skill) -> Skill:
    """卸载磁盘 Skill 包：保留文件，只从已安装目录拿掉。"""

    _upsert_external_override(skill.id, installed=False, enabled=False)
    return skill.model_copy(update={
        "status": "available",
        "enabled": False,
        "installed_at": None,
        "updated_at": _utc_now(),
    })


def delete_registered_skill_files(skill: Skill) -> None:
    """删除用户注册 Skill 的包目录。"""

    if skill.resource_path and os.path.isdir(skill.resource_path):
        shutil.rmtree(skill.resource_path)


def delete_external_skill(skill: Skill, *, project_root: Path, workspace_id: str) -> None:
    """删除当前工作区扫描到的 Skill 包。系统包不能删。"""

    if skill.id in _builtin_skills or skill.id in SYSTEM_SKILL_IDS or skill.source_scope == "system":
        raise HTTPException(status_code=409, detail="Built-in Skill cannot be deleted")
    package = Path(skill.resource_path).resolve()
    if not _package_is_deletable(package, project_root, workspace_id):
        raise HTTPException(status_code=409, detail="只能删除工作区里的 Skill 包")
    shutil.rmtree(package)
    _external_skill_overrides.pop(skill.id, None)
    delete_json_record(EXTERNAL_SKILL_OVERRIDES_NAMESPACE, skill.id)


def _package_is_deletable(package: Path, project_root: Path, workspace_id: str) -> bool:
    if not (package / "SKILL.md").is_file():
        return False
    from src.catalog.paths import workspace_catalog_root

    roots: list[Path] = []
    bases = [project_root, tenant_root()]
    home = runtime_user_home_root()
    if home is not None:
        bases.append(Path(home))
    for base in bases:
        for name in (".agents/skills", ".agent/skills", ".claude/skills"):
            roots.append(Path(base) / name)
    try:
        roots.append(workspace_catalog_root(workspace_id) / "skills")
    except ValueError:
        pass
    for root in roots:
        try:
            resolved = root.resolve()
        except OSError:
            continue
        if resolved.is_dir() and package.is_relative_to(resolved):
            return True
    return False


def _load_external_skill_overrides() -> None:
    """加载 enabled 与 availableToAllAgents 覆盖。"""

    _external_skill_overrides.clear()
    for item in list_json_records(EXTERNAL_SKILL_OVERRIDES_NAMESPACE):
        skill_id = str(item.get("id") or "")
        if not skill_id:
            continue
        record: dict[str, bool] = {}
        if "enabled" in item:
            record["enabled"] = bool(item["enabled"])
        if "availableToAllAgents" in item:
            record["availableToAllAgents"] = bool(item["availableToAllAgents"])
        if "installed" in item:
            record["installed"] = bool(item["installed"])
        if record:
            _external_skill_overrides[skill_id] = record


def _save_user_skills() -> None:
    records: dict[str, dict] = {}
    for skill_id, skill in _skills.items():
        if skill_id in _builtin_skills:
            continue
        payload = skill.model_dump(by_alias=True)
        payload["content"] = ""
        records[skill_id] = payload
    replace_skills(records)


def _persist_if_user_skill(skill_id: str) -> None:
    if skill_id not in _builtin_skills:
        _save_user_skills()


def register_user_skill(data: SkillRegisterRequest) -> Skill:
    """注册标准用户 Skill；后台流程和 API 路由共用。"""

    name = str(data.name or "").strip()
    if name in SYSTEM_SKILL_IDS or _slugify(name) in SYSTEM_SKILL_IDS:
        raise HTTPException(status_code=409, detail=f"系统 Skill {name} 不能被用户注册")
    _ensure_skill_name_available(data.name)
    skill_id = _unique_skill_id(data.name)
    markdown = _skill_markdown_from_registration(data)
    resource_path, resources = _write_skill_package(data.name, markdown)
    now = _utc_now()
    skill = Skill.model_validate(
        {
            "id": skill_id,
            "name": data.name,
            "description": data.description or f"用户注册的 Skill: {data.name}",
            "category": data.category or "自定义",
            "trigger": data.trigger,
            "content": markdown,
            "tags": data.tags[:8],
            "resourcePath": resource_path,
            "resources": resources,
            "status": "installed",
            "enabled": True,
            "installedAt": now,
            "updatedAt": now,
            "workspaceId": data.workspace_id,
        }
    )
    _skills[skill.id] = skill
    _save_user_skills()
    return skill


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


def resolve_runtime_skill_paths(
    skills: list[Skill],
    *,
    runtime_context: object | None = None,
) -> list[str]:
    """把已裁剪的持久 Skill 转换为 AgentScope 可加载的安全包目录。"""

    packages_root = os.path.realpath(_packages_dir())
    trusted_roots = [packages_root]
    trusted_entry_roots: list[str] = []
    if runtime_context is not None:
        project_root = Path(str(getattr(runtime_context, "project_root", ""))).resolve()
        user_root = tenant_root().resolve()
        trusted_entry_roots.extend(
            os.path.abspath(str(project_root / relative))
            for relative in (".agents/skills", ".agent/skills", ".claude/skills")
        )
        trusted_entry_roots.extend(
            os.path.abspath(str(user_root / relative))
            for relative in (".agents/skills", ".agent/skills", ".claude/skills")
        )
        home_root = runtime_user_home_root()
        if home_root is not None:
            trusted_entry_roots.extend(
                os.path.abspath(str(home_root / relative))
                for relative in (".agents/skills", ".agent/skills", ".claude/skills")
            )
        trusted_roots.extend(os.path.realpath(path) for path in trusted_entry_roots)
    paths: list[str] = []
    seen: set[str] = set()
    for skill in skills:
        if not skill.resource_path:
            continue
        package_dir = os.path.realpath(os.path.abspath(skill.resource_path))
        entry_path = os.path.abspath(skill.resource_path)
        try:
            inside_packages_root = any(
                os.path.commonpath([trusted_root, package_dir]) == trusted_root
                for trusted_root in trusted_roots
            )
            inside_trusted_entry = any(
                os.path.commonpath([trusted_root, entry_path]) == trusted_root
                for trusted_root in trusted_entry_roots
            )
        except ValueError:
            inside_packages_root = False
            inside_trusted_entry = False
        if (
            not inside_packages_root
            and not inside_trusted_entry
            or package_dir in seen
            or not os.path.isdir(package_dir)
            or not os.path.isfile(os.path.join(package_dir, "SKILL.md"))
        ):
            continue
        seen.add(package_dir)
        paths.append(package_dir)
    return sorted(paths)


def get_installed_skill(skill_id: str) -> Skill | None:
    """按 ID 获取已安装启用的 Skill。"""
    skill = _skills.get(skill_id)
    if not skill or skill.status != "installed" or not skill.enabled:
        return None
    return _with_share_flag(refresh_skill_package(skill))


def list_installed_enabled_skills() -> list[Skill]:
    """已安装且启用的登记 Skill，供 Chat 目录合并共享项。"""

    return [
        _with_share_flag(refresh_skill_package(skill))
        for skill in _skills.values()
        if skill.status == "installed" and skill.enabled
    ]


def resolve_installed_skills(
    skill_refs: list[str],
    *,
    external_skills: list[Skill] | None = None,
) -> tuple[list[Skill], list[str]]:
    """按 ID 或名称解析已安装启用的 Skill，保留输入顺序并返回未命中引用。"""

    resolved: list[Skill] = []
    missing: list[str] = []
    seen: set[str] = set()
    enabled_skills = [
        skill
        for skill in _skills.values()
        if skill.status == "installed" and skill.enabled
    ]
    enabled_skills.extend(skill for skill in (external_skills or []) if skill.status == "installed" and skill.enabled)
    by_normalized_name = {
        _normalized_skill_name(skill.name): skill
        for skill in enabled_skills
    }
    for raw_ref in skill_refs:
        ref = str(raw_ref or "").strip()
        if not ref:
            continue
        skill = get_installed_skill(ref) or by_normalized_name.get(_normalized_skill_name(ref))
        if not skill:
            missing.append(ref)
            continue
        if skill.id in seen:
            continue
        seen.add(skill.id)
        resolved.append(skill)
    return resolved, missing


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

    if len(loaded_skills) == 1 and explicit_skill_request:
        return loaded_skills

    return []


def _get_loaded_skills(skill_ids: list[str]) -> list[Skill]:
    return [
        refresh_skill_package(_skills[skill_id])
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


def _install_skill_registry() -> None:
    """第一次访问当前租户时再加载用户 Skill。"""

    global _skills, _external_skill_overrides
    from src.tenancy.registry import TenantDictProxy, TenantScoped

    _skills = TenantDictProxy(TenantScoped(lambda: dict(_builtin_skills)), _load_user_skills)  # type: ignore[assignment]
    _external_skill_overrides = TenantDictProxy(TenantScoped(dict), None)  # type: ignore[assignment]


_install_skill_registry()

