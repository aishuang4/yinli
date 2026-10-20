"""旧 Skill 接口的兼容门面；存在性/策略/正文只委托文件领域服务。"""
from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from datetime import datetime, timezone

from fastapi import HTTPException

from src.skills.api_models import Skill, SkillInstallStatus, SkillInstallRequest, SkillShareRequest, SkillRegisterRequest
from src.catalog.skills import ALWAYS_INJECTED_SKILL_IDS, SYSTEM_SKILL_IDS, DiscoveredSkill, discover_skill_packages, read_package_markdown
from src.config.runtime_paths import tenant_root, skill_packages_root, user_home_root as runtime_user_home_root
from src.skills import build_skill_catalog, current_skill_context, read_skill, resolve_skill, SkillError
from src.skills.service import catalog_for, get_catalog_skill, list_catalog_skills, publish_skill, registration_markdown, skill_from_candidate
from src.skills.policy import set_skill_policy


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _normalized_skill_name(value):
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", value.lower())


def _slugify(value):
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "custom-skill"


def _is_share_locked(skill_id):
    return skill_id == "yinli-catalog"


def _with_share_flag(skill):
    return skill.model_copy(update={"available_to_all_agents": True}) if _is_share_locked(skill.id) else skill


def _hydrate_skill(skill):
    if skill._resolved_skill:
        return skill.model_copy(update={"content": read_skill(skill._resolved_skill).markdown})
    # 兼容纯模型调用必须重新定位文件，绝不回退 content。
    return get_catalog_skill(skill.id, catalog_for())


def discover_external_skills(project_root, *, user_data_root=None, user_home_root=None, workspace_id="default"):
    context = current_skill_context()
    if user_data_root is not None:
        context = context.model_copy(update={"tenant_root": Path(user_data_root), "home_root": user_home_root})
    return list_catalog_skills(build_skill_catalog(context))


def get_runtime_skill_catalog(project_root, *, workspace_id="default"):
    return discover_external_skills(project_root, workspace_id=workspace_id)


def get_installed_skill(skill_id, *, workspace_id="default", project_root=None):
    try:
        return get_catalog_skill(skill_id, catalog_for(), require_enabled=True)
    except SkillError:
        return None


def resolve_project_skill(project_root, skill_ref, *, source_path="", workspace_id="default"):
    try:
        skill = get_catalog_skill(skill_ref, catalog_for(), require_enabled=True)
        if source_path and Path(source_path).resolve() != Path(skill.source_path).resolve():
            return None
        return skill
    except SkillError:
        return None


def list_installed_enabled_skills(*, workspace_id="default"):
    return [item for item in list_catalog_skills(catalog_for()) if item.status == "installed" and item.enabled and item.resolution_state != "shadowed"]


def resolve_installed_skills(skill_refs, *, external_skills=None):
    # external_skills 是同一 catalog 的投影，仍使用原快照 resolver，不能按名称覆写。
    supplied = list(external_skills or [])
    catalog = next((item._catalog_snapshot for item in supplied if getattr(item, "_catalog_snapshot", None)), None)
    catalog = catalog or catalog_for()
    selected, missing, seen = [], [], set()
    for ref in skill_refs:
        try:
            target = resolve_skill(ref, catalog)
            skill = next((item for item in supplied if item.id == target.candidate.id), None) or skill_from_candidate(target.candidate, catalog)
            if skill.id not in seen:
                selected.append(skill)
                seen.add(skill.id)
        except SkillError:
            missing.append(ref)
    return selected, missing


def _project_root_from_skill(skill):
    root = getattr(skill, "_catalog_context", None)
    if root:
        return root.project_root
    selected = getattr(skill, "_resolved_skill", None)
    if selected and selected.candidate.root.scope == "project":
        return selected.candidate.root.path.parent.parent
    return None


def resolve_runtime_skill_paths(skills, *, runtime_context=None):
    from src.skills.sdk import SkillRuntimePath
    result = []
    for skill in skills:
        selected = getattr(skill, "_resolved_skill", None)
        if not selected:
            catalog = catalog_for()
            selected = resolve_skill(skill.id, catalog)
        if not selected.candidate.enabled or not selected.candidate.installed:
            raise SkillError("skill_disabled", "Skill 已停用或卸载")
        result.append(SkillRuntimePath(selected))
    return result


def list_skill_usage_details(skills):
    return [{"id": skill.id, "name": skill.name, "loadName": skill.load_name or skill.name,
             "description": skill.description, "trigger": skill.trigger, "content": skill.content,
             "resourcePath": skill.resource_path, "resources": skill.resources,
             "sourcePath": skill.source_path, "sourceScope": skill.source_scope, "contentHash": skill.content_hash}
            for skill in skills]


def register_user_skill(data):
    try:
        return publish_skill(current_skill_context(), data.name, registration_markdown(data))
    except SkillError as exc:
        raise HTTPException(status_code=409 if exc.code in {"skill_exists", "skill_reserved_name"} else 422, detail=exc.as_dict()) from exc


class FileSkillView(Mapping):
    """只读兼容映射，不再保存登记副本。"""

    def __iter__(self):
        return iter(self._items())

    def __len__(self):
        return len(self._items())

    def __getitem__(self, key):
        try:
            return get_catalog_skill(key, catalog_for())
        except SkillError as exc:
            raise KeyError(key) from exc

    def _items(self):
        return {item.id: item for item in list_catalog_skills(catalog_for())}

    def values(self):
        return self._items().values()

    def get(self, key, default=None):
        try:
            return self[key]
        except SkillError:
            return default


_skills = FileSkillView()
_builtin_skills = {key: None for key in SYSTEM_SKILL_IDS}
# 兼容旧调用名称；无数据库加载和反向重建行为。
def _load_user_skills():
    return None


def _get_loaded_skills(skill_ids):
    return resolve_installed_skills(skill_ids)[0]


def select_relevant_skills(skill_ids, message):
    return _get_loaded_skills(skill_ids)


def build_skill_system_prompt(skill_ids):
    return build_skill_system_prompt_from_skills(_get_loaded_skills(skill_ids))


def build_skill_system_prompt_from_skills(skills):
    """兼容返回索引，说明书正文只进会话。"""
    return "\n".join(f"- {item.load_name or item.name}: {item.description} ({item.resource_path})" for item in skills)
