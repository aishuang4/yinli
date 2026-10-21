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
    scan_skill_directories,
    skill_scan_targets,
)
from src.config.runtime_paths import (
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
from src.storage.database import (  # 路由仍从本模块再导出这些名字
    get_project as get_project,
    delete_json_record as delete_json_record,
    get_json_record as get_json_record,
    list_json_records as list_json_records,
    upsert_json_record as upsert_json_record,
)

logger = logging.getLogger(__name__)


def _packages_dir() -> str:
    """当前租户的 Skill 包根。"""

    return str(skill_packages_root())


def _discover_packages(*args, **kwargs):
    """读取路由模块上的发现函数，测试会替换那个名字。"""

    from src.server.routes.skills import discover_skill_packages

    return discover_skill_packages(*args, **kwargs)


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
    source_kind: str = Field(default="native", alias="sourceKind")
    source_scope: str = Field(default="system", alias="sourceScope")
    source_path: str = Field(default="", alias="sourcePath")
    content_hash: str = Field(default="", alias="contentHash")
    shadowed_by: str = Field(default="", alias="shadowedBy")

    model_config = {"populate_by_name": True, "extra": "ignore"}


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

    model_config = {"populate_by_name": True, "extra": "ignore"}


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


_builtin_skills: dict[str, Skill] = {}
_skills: dict[str, Skill] = dict(_builtin_skills)


def _is_share_locked(skill_id: str) -> bool:
    """yinli-catalog 对合格 Chat 锁定全体可用。"""

    return skill_id in ALWAYS_INJECTED_SKILL_IDS


def _override_record(skill_id: str) -> dict[str, bool]:
    return dict(_external_skill_overrides.get(skill_id) or {})


_OVERRIDE_FIELDS = ("enabled", "installed", "availableToAllAgents")


def _upsert_external_override(skill_id: str, **fields: object) -> dict[str, bool]:
    """写入 skill_overrides 表。只保留 enabled / installed / availableToAllAgents，其它关键字丢弃。"""

    from src.storage.database import write_tx

    current = _override_record(skill_id)
    current.update({key: bool(value) for key, value in fields.items() if key in _OVERRIDE_FIELDS})
    _external_skill_overrides[skill_id] = current
    enabled = current.get("enabled")
    share = current.get("availableToAllAgents")
    installed = current.get("installed")
    with write_tx() as conn:
        row = conn.execute(
            """
            SELECT enabled, available_to_all_agents, installed
            FROM skill_overrides WHERE skill_id = ?
            """,
            (skill_id,),
        ).fetchone()
        if enabled is None:
            enabled = bool(row["enabled"]) if row else True
        if share is None:
            share = bool(row["available_to_all_agents"]) if row else True
        if installed is None:
            installed = bool(row["installed"]) if row else True
        conn.execute(
            """
            INSERT INTO skill_overrides (
                skill_id, enabled, available_to_all_agents, installed, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(skill_id) DO UPDATE SET
                enabled = excluded.enabled,
                available_to_all_agents = excluded.available_to_all_agents,
                installed = excluded.installed,
                updated_at = excluded.updated_at
            """,
            (skill_id, int(bool(enabled)), int(bool(share)), int(bool(installed)), _utc_now()),
        )
    return current


def delete_skill_override(skill_id: str) -> None:
    """删掉一条策略覆盖。列表接口若只清内存，重扫会从这张表把记录读回来。"""

    from src.storage.database import write_tx

    _external_skill_overrides.pop(skill_id, None)
    with write_tx() as conn:
        conn.execute("DELETE FROM skill_overrides WHERE skill_id = ?", (skill_id,))


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
        "content_hash": hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
        "updated_at": updated_at,
    })


def _hydrate_skill(skill: Skill) -> Skill:
    """API / 运行时正文从 SKILL.md 读取，不信任 sqlite content。"""

    return refresh_skill_package(skill)


def _category_from_package(item: DiscoveredSkill) -> str:
    """系统包固定为系统。用户包保留 SKILL.md 里的 category，没有则记为外部导入。"""

    if item.source_scope == "system":
        return "系统"
    match = re.match(r"\A---\s*\n(?P<yaml>.*?)\n---\s*\n?", item.markdown, re.DOTALL)
    if match:
        try:
            loaded = yaml.safe_load(match.group("yaml")) or {}
        except yaml.YAMLError:
            loaded = {}
        if isinstance(loaded, dict):
            category = str(loaded.get("category") or "").strip()
            if category:
                return category[:40]
    return "外部导入"


def _skill_from_discovered(item: DiscoveredSkill) -> Skill:
    return Skill.model_validate({
        "id": item.id,
        "name": item.name,
        "description": item.description,
        "category": _category_from_package(item),
        "trigger": item.trigger,
        "content": item.markdown,
        "tags": [item.source_scope, item.source_kind],
        "resourcePath": item.resource_path,
        "resources": item.resources,
        "warnings": [],
        "status": "installed",
        "enabled": item.enabled,
        "updatedAt": _utc_now(),
        "sourceKind": item.source_kind,
        "sourceScope": item.source_scope,
        "sourcePath": item.source_path,
        "contentHash": item.content_hash,
        "shadowedBy": item.shadowed_by,
    })


def _system_skill_map() -> dict[str, Skill]:
    """seed 系统包并返回不可删除的内置 Skill。不直接改进程缓存。"""

    from src.catalog.skill_seed import seed_system_skills
    from src.catalog.skills import list_system_skill_packages

    seed_system_skills()
    found: dict[str, Skill] = {}
    for item in list_system_skill_packages():
        skill = _skill_from_discovered(item).model_copy(update={
            "enabled": True,
            "status": "installed",
            "category": "系统",
            "available_to_all_agents": True,
        })
        found[skill.id] = _with_share_flag(skill)
    return found


def _folder_paths(project: dict) -> list[Path]:
    """授权目录优先；没有 folders 时用 primaryPath。不读旧的根路径字段。"""

    paths: list[Path] = []
    for folder in project.get("folders") or []:
        if isinstance(folder, dict):
            raw = str(folder.get("path") or "").strip()
        else:
            raw = str(folder or "").strip()
        if raw:
            paths.append(Path(raw))
    if paths:
        return paths
    primary = str(project.get("primaryPath") or project.get("primary_path") or "").strip()
    return [Path(primary)] if primary else []


def _project_scan_roots(extra: Path | None = None) -> list[Path]:
    seen: set[str] = set()
    roots: list[Path] = []

    def _add(path: Path) -> None:
        try:
            key = str(path.expanduser().resolve())
        except OSError:
            key = str(path)
        if key in seen:
            return
        seen.add(key)
        roots.append(path)

    if extra is not None:
        _add(Path(extra))
    try:
        from src.storage.database import list_projects

        for project in list_projects():
            for path in _folder_paths(project):
                _add(path)
    except Exception:
        logger.exception("读取 Project 授权目录失败，Skill 扫描跳过项目目录")
    return roots


def _replace_mapping(target: dict, fresh: dict) -> None:
    """先建好的字典整体换上，避免先清空再填充时读到空缓存。"""

    scoped = getattr(target, "_scoped", None)
    replace = getattr(scoped, "replace", None)
    if callable(replace):
        replace(fresh)
        mark = getattr(target, "mark_loaded", None)
        if callable(mark):
            mark()
        return
    stale = [key for key in list(target) if key not in fresh]
    target.update(fresh)
    for key in stale:
        target.pop(key, None)


def reload_skill_cache(project_root: Path | None = None) -> None:
    """先建新缓存再整体替换。导入、更新、删除和启动都走这里。"""

    ensure_tenant_directories()
    overrides = _read_skill_overrides()
    _replace_mapping(_external_skill_overrides, overrides)
    builtin = _system_skill_map()
    skills: dict[str, Skill] = dict(builtin)
    for item in scan_skill_directories(skill_scan_targets(
        _project_scan_roots(project_root),
        runtime_root=skill_packages_root(),
    )):
        skill = _skill_from_discovered(item)
        record = overrides.get(skill.id, {})
        installed, enabled = _external_install_state(item, record)
        if skill.id in SYSTEM_SKILL_IDS:
            builtin[skill.id] = skill.model_copy(update={
                "enabled": True,
                "status": "installed",
                "category": "系统",
                "available_to_all_agents": True,
            })
            installed, enabled = True, True
        share = True if _is_share_locked(skill.id) else (
            bool(record["availableToAllAgents"]) if "availableToAllAgents" in record else True
        )
        decorated = _with_share_flag(skill.model_copy(update={
            "status": "installed" if installed else "available",
            "enabled": enabled,
            "available_to_all_agents": share,
        }))
        key = skill.id if not decorated.shadowed_by else f"{skill.id}@{skill.source_path}"
        skills[key] = decorated
    stale = [key for key in list(_builtin_skills) if key not in builtin]
    _builtin_skills.update(builtin)
    for key in stale:
        _builtin_skills.pop(key, None)
    _replace_mapping(_skills, skills)


def _load_user_skills() -> None:
    reload_skill_cache()


def cached_skill_records() -> list[dict]:
    """给仍从存储模块读目录的调用方。内容来自本次扫描，不是 skills 表。"""

    return [skill.model_dump(by_alias=True) for skill in _skills.values()]


def discover_external_skills(
    project_root: Path,
    *,
    user_data_root: Path | None = None,
    user_home_root: Path | None = None,
    **_extra: object,
) -> list[Skill]:
    """发现标准 Agent/Claude Skill 包；项目级默认启用，用户级默认关闭。

    user_home_root 为 None（hosted）时跳过 HOME scope（§12.7）。
    """

    del _extra
    discovered: list[Skill] = []
    for item in _discover_packages(
        Path(project_root),
        user_data_root=Path(user_data_root) if user_data_root is not None else tenant_root(),
        user_home_root=Path(user_home_root) if user_home_root is not None else runtime_user_home_root(),
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
    """返回当前缓存。缓存只来自约定目录的最近一次扫描。"""

    reload_skill_cache(project_root)
    return [
        refresh_skill_package(skill)
        for skill in _skills.values()
        if not skill.shadowed_by
    ]


def find_skill(
    skill_id: str,
    project_root: Path | None,
    **_extra: object,
) -> Skill | None:
    """按 ID 查找已登记 Skill，或当前 Project 磁盘上的 Skill 包。"""

    del _extra
    if skill_id in _skills:
        skill = _skills[skill_id]
        return _with_share_flag(refresh_skill_package(skill))
    if project_root is None:
        return None
    for skill in discover_external_skills(project_root):
        if skill.id == skill_id and not skill.shadowed_by:
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


def delete_external_skill(skill: Skill, *, project_root: Path, **_extra: object) -> None:
    """删除当前扫描到的 Skill 包。系统包不能删。"""

    del _extra
    if skill.id in _builtin_skills or skill.id in SYSTEM_SKILL_IDS or skill.source_scope == "system":
        raise HTTPException(status_code=409, detail="Built-in Skill cannot be deleted")
    package = Path(skill.resource_path).resolve()
    if not _package_is_deletable(package, project_root):
        raise HTTPException(status_code=409, detail="只能删除工作区里的 Skill 包")
    shutil.rmtree(package)
    delete_skill_override(skill.id)


def _package_is_deletable(package: Path, project_root: Path) -> bool:
    if not (package / "SKILL.md").is_file():
        return False
    roots: list[Path] = [skill_packages_root()]
    bases = [project_root, tenant_root(), *_project_scan_roots()]
    home = runtime_user_home_root()
    if home is not None:
        bases.append(Path(home))
    for base in bases:
        for name in (".agents/skills", ".agent/skills", ".claude/skills"):
            roots.append(Path(base) / name)
    for root in roots:
        try:
            resolved = root.resolve()
        except OSError:
            continue
        if resolved.is_dir() and package.is_relative_to(resolved):
            return True
    return False


def _read_skill_overrides() -> dict[str, dict[str, bool]]:
    """从 skill_overrides 表读策略覆盖，不改进程缓存。"""

    from src.storage.database import read_conn

    loaded: dict[str, dict[str, bool]] = {}
    try:
        with read_conn() as conn:
            rows = conn.execute(
                """
                SELECT skill_id, enabled, available_to_all_agents, installed
                FROM skill_overrides
                """
            ).fetchall()
    except Exception:
        logger.exception("读取 skill_overrides 失败")
        return {}
    for row in rows:
        skill_id = str(row["skill_id"] or "")
        if not skill_id:
            continue
        loaded[skill_id] = {
            "enabled": bool(row["enabled"]),
            "availableToAllAgents": bool(row["available_to_all_agents"]),
            "installed": bool(row["installed"]),
        }
    return loaded


def _load_external_skill_overrides() -> None:
    """加载 enabled 与 availableToAllAgents 覆盖。"""

    _replace_mapping(_external_skill_overrides, _read_skill_overrides())


def _save_user_skills() -> None:
    """兼容旧调用：技能不写回数据库，重新扫描目录刷新缓存。"""

    reload_skill_cache()


def _persist_if_user_skill(skill_id: str) -> None:
    del skill_id
    reload_skill_cache()


def register_user_skill(data: SkillRegisterRequest) -> Skill:
    """注册标准用户 Skill；后台流程和 API 路由共用。"""

    name = str(data.name or "").strip()
    if name in SYSTEM_SKILL_IDS or _slugify(name) in SYSTEM_SKILL_IDS:
        raise HTTPException(status_code=409, detail=f"系统 Skill {name} 不能被用户注册")
    _ensure_skill_name_available(data.name)
    markdown = _skill_markdown_from_registration(data)
    resource_path, _resources = _write_skill_package(data.name, markdown)
    reload_skill_cache()
    wanted = str(Path(resource_path).resolve())
    for skill in _skills.values():
        if skill.resource_path and str(Path(skill.resource_path).resolve()) == wanted:
            return skill
    raise HTTPException(status_code=500, detail="写入后的 Skill 没有被扫描到")


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
        if skill.status == "installed" and skill.enabled and not skill.shadowed_by
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
        if skill.status == "installed" and skill.enabled and not skill.shadowed_by
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

