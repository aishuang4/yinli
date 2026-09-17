"""Skill 包发现。正文只来自 SKILL.md，不依赖 sqlite content。

禁止 import `src.server.routes`。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from src.catalog.paths import workspace_catalog_root
from src.config.runtime_paths import SKILL_PACKAGES_ROOT

SYSTEM_SKILL_IDS = ("yinli-catalog", "yinli-charts", "word-documents")
# 合格 Chat 无条件追加的系统说明书（file-catalog-v1 §9.2）；其余包可经 availableToAllAgents 共享，或写入 Agent 配置。
ALWAYS_INJECTED_SKILL_IDS = ("yinli-catalog",)
_SKILL_DIR_NAMES = (".agents/skills", ".agent/skills", ".claude/skills")
_FRONTMATTER_RE = re.compile(r"\A---\s*\n(?P<yaml>.*?)\n---\s*\n?(?P<body>.*)\Z", re.DOTALL)


@dataclass(frozen=True)
class DiscoveredSkill:
    """磁盘上的一个 Skill 包。"""

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


def read_package_markdown(resource_path: str | Path) -> str:
    """读取包内 SKILL.md；缺失则空字符串。"""

    skill_md = Path(resource_path) / "SKILL.md"
    if not skill_md.is_file():
        return ""
    try:
        return skill_md.read_text(encoding="utf-8")
    except OSError:
        return ""


def _split_frontmatter(markdown: str) -> tuple[dict, str]:
    match = _FRONTMATTER_RE.match(markdown)
    if not match:
        return {}, markdown
    try:
        payload = yaml.safe_load(match.group("yaml")) or {}
    except yaml.YAMLError:
        return {}, markdown
    if not isinstance(payload, dict):
        return {}, markdown
    return payload, match.group("body")


def _package_resources(package_dir: Path) -> list[str]:
    return sorted(
        str(path.relative_to(package_dir))
        for path in package_dir.rglob("*")
        if path.is_file()
    )


def _discovered_from_package(
    package_dir: Path,
    *,
    enabled: bool,
    source_kind: str,
    source_scope: str,
    stable_id: str | None = None,
) -> DiscoveredSkill | None:
    skill_md = package_dir / "SKILL.md"
    if not skill_md.is_file():
        return None
    try:
        markdown = skill_md.read_text(encoding="utf-8")
    except OSError:
        return None
    metadata, body = _split_frontmatter(markdown)
    name = str(metadata.get("name") or package_dir.name).strip()
    description = str(metadata.get("description") or "").strip()
    if not name or not description:
        return None
    content_hash = hashlib.sha256(markdown.encode("utf-8")).hexdigest()[:16]
    skill_id = stable_id or f"external-{content_hash}"
    return DiscoveredSkill(
        id=skill_id,
        name=name,
        description=description,
        trigger=str(metadata.get("trigger") or description),
        markdown=markdown.strip() or body,
        resource_path=str(package_dir),
        resources=_package_resources(package_dir),
        enabled=enabled,
        source_kind=source_kind,
        source_scope=source_scope,
        source_path=str(skill_md),
        content_hash=content_hash,
    )


def discover_skill_packages(
    project_root: Path,
    *,
    user_data_root: Path,
    user_home_root: Path,
    workspace_id: str = "default",
) -> list[DiscoveredSkill]:
    """扫描项目 / 用户 / Workspace catalog 下的 Skill 包。"""

    roots: list[tuple[Path, str, bool, str]] = []
    for base, scope, enabled, kind in (
        (project_root, "project", True, "external_agents"),
        (user_data_root, "user", False, "external_agents"),
        (user_home_root, "user", False, "external_agents"),
    ):
        for directory_name in _SKILL_DIR_NAMES:
            roots.append((Path(base) / directory_name, scope, enabled, kind))
    try:
        roots.append((workspace_catalog_root(workspace_id) / "skills", "user", False, "workspace"))
    except ValueError:
        pass

    discovered: list[DiscoveredSkill] = []
    seen: set[str] = set()
    for root, scope, enabled, source_kind in roots:
        try:
            resolved = root.resolve()
        except OSError:
            continue
        if not resolved.is_dir():
            continue
        for skill_md in sorted(resolved.glob("*/SKILL.md")):
            package_dir = skill_md.parent.absolute()
            try:
                if not package_dir.is_relative_to(resolved):
                    continue
            except ValueError:
                continue
            item = _discovered_from_package(
                package_dir,
                enabled=enabled,
                source_kind=source_kind,
                source_scope=scope,
            )
            if item is None or item.id in seen:
                continue
            seen.add(item.id)
            discovered.append(item)
    return discovered


def list_system_skill_packages() -> list[DiscoveredSkill]:
    """SKILL_PACKAGES_ROOT 中的系统包。"""

    items: list[DiscoveredSkill] = []
    root = Path(SKILL_PACKAGES_ROOT)
    for skill_id in SYSTEM_SKILL_IDS:
        item = _discovered_from_package(
            root / skill_id,
            enabled=True,
            source_kind="system",
            source_scope="system",
            stable_id=skill_id,
        )
        if item is not None:
            items.append(item)
    return items


def project_skill_refs(project_root: Path) -> set[str]:
    """当前 Project `.agents/skills` 等目录下默认可运行的 id 与 name。"""

    refs: set[str] = set()
    root = Path(project_root)
    for directory_name in _SKILL_DIR_NAMES:
        for skill_md in sorted((root / directory_name).glob("*/SKILL.md")) if (root / directory_name).is_dir() else []:
            item = _discovered_from_package(
                skill_md.parent.absolute(),
                enabled=True,
                source_kind="external_agents",
                source_scope="project",
            )
            if item is None:
                continue
            refs.add(item.id)
            refs.add(item.name)
    return refs
