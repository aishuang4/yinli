"""旧 catalog Skill API 的文件领域兼容投影；不保留第二套扫描规则。"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from src.skills.catalog import SYSTEM_SKILL_IDS, build_skill_catalog, current_skill_context, stable_skill_id
from src.skills.models import SkillError, SkillSourceRoot
from src.skills.parser import parse_manual
from src.config.runtime_paths import skill_packages_root

ALWAYS_INJECTED_SKILL_IDS = ("yinli-catalog",)


@dataclass(frozen=True)
class DiscoveredSkill:
    """兼容旧文件发现接口的只读投影。"""
    id: str
    name: str
    description: str
    trigger: str
    markdown: str
    resource_path: str
    resources: list[str] = field(default_factory=list)
    enabled: bool = True
    source_kind: str = "external_agents"
    source_scope: str = "project"
    source_path: str = ""
    content_hash: str = ""
    load_name: str = ""


def read_package_skill_bytes(resource_path):
    try:
        return (Path(resource_path) / "SKILL.md").read_bytes()
    except OSError:
        return b""


def package_skill_sha256(resource_path):
    raw = read_package_skill_bytes(resource_path)
    return hashlib.sha256(raw).hexdigest() if raw else ""


def read_package_markdown(resource_path):
    try:
        return parse_manual(read_package_skill_bytes(resource_path), Path(resource_path).name).markdown
    except SkillError:
        return ""


def _discovered_from_package(package_dir, *, enabled, source_kind, source_scope, stable_id=None):
    """仅供历史调用显式读取一个包；所有目录冲突由 build_skill_catalog 处理。"""
    directory = Path(package_dir).resolve()
    try:
        manual = parse_manual((directory / "SKILL.md").read_bytes(), directory.name)
        from src.skills.catalog import _resources
        resources = _resources(directory)
    except (OSError, SkillError):
        return None
    root = SkillSourceRoot(key=f"explicit-{source_kind}", owner_key=source_scope, path=directory.parent,
                           priority=0, scope=source_scope, kind=source_kind)
    return DiscoveredSkill(id=stable_id or stable_skill_id(root, directory.name), name=manual.name,
                          description=manual.description, trigger=str(manual.metadata.get("trigger") or manual.description),
                          markdown=manual.markdown, resource_path=str(directory), resources=list(resources), enabled=enabled,
                          source_kind=source_kind, source_scope=source_scope, source_path=str(directory / "SKILL.md"),
                          content_hash=manual.content_hash)


def _project_context(project_root, workspace_id="default"):
    return current_skill_context()


def discover_skill_packages(project_root, *, user_data_root, user_home_root, workspace_id="default"):
    context = _project_context(project_root, workspace_id).model_copy(update={"tenant_root": Path(user_data_root), "home_root": user_home_root})
    catalog = build_skill_catalog(context)
    from src.skills.reader import read_skill
    from src.skills.catalog import resolve_skill
    return [DiscoveredSkill(id=item.id, name=item.name, description=item.description, trigger=item.trigger,
                            markdown=read_skill(resolve_skill(item.id, catalog, require_enabled=False)).markdown,
                            resource_path=str(item.package_path), resources=list(item.resources), enabled=item.enabled,
                            source_kind=item.root.kind, source_scope=item.root.scope, source_path=str(item.source_path),
                            content_hash=item.content_hash, load_name=item.load_name)
            for item in catalog.candidates if item.state != "invalid"]


def discover_project_skill_packages(project_root):
    """兼容旧调用；Project 不再拥有 Skill 包来源。"""

    return []


def list_system_skill_packages():
    return [item for skill_id in SYSTEM_SKILL_IDS if (item := _discovered_from_package(
        skill_packages_root() / skill_id, enabled=True, source_kind="system", source_scope="system", stable_id=skill_id))]


def project_skill_refs(project_root):
    """兼容旧调用；Project Skill 自动放行已删除。"""

    return set()
