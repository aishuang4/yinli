"""Skill 包发现。正文只来自 SKILL.md，不依赖 sqlite content。

禁止 import `src.server.routes`。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, replace
from pathlib import Path

import yaml

from src.config.runtime_paths import skill_packages_root, tenant_root

SYSTEM_SKILL_IDS = ("yinli-catalog", "yinli-charts", "word-documents")
# 只掃描這類目錄。額外位置走 register_skill_scan_root，不從資料庫補技能。
SKILL_SCAN_DIR_NAMES = (".agents/skills", ".agent/skills", ".claude/skills")
_extra_skill_roots: list[Path] = []
# 合格 Chat 无条件追加的系统说明书（file-catalog-v1 §9.2）；其余包可经 availableToAllAgents 共享，或写入 Agent 配置。
ALWAYS_INJECTED_SKILL_IDS = ("yinli-catalog",)
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
    shadowed_by: str = ""


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
    content_hash = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
    skill_id = stable_id or f"external-{content_hash[:16]}"
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
    user_home_root: Path | None = None,
    **_extra: object,
) -> list[DiscoveredSkill]:
    """只扫描 runtime skills 与 .agents/.agent/.claude/skills。顺序是 runtime → user → project。"""

    del user_home_root, _extra
    targets: list[tuple[Path, str, bool, str]] = [
        (skill_packages_root(), "runtime", True, "runtime"),
    ]
    bases: list[tuple[Path, str, bool, str]] = [
        (user_data_root, "user", False, "external_agents"),
        (project_root, "project", True, "external_agents"),
    ]
    for base, scope, enabled, kind in bases:
        for directory_name in SKILL_SCAN_DIR_NAMES:
            targets.append((Path(base) / directory_name, scope, enabled, kind))
    for extra in _extra_skill_roots:
        targets.append((Path(extra), "extra", True, "extension"))
    return scan_skill_directories(targets)


def list_system_skill_packages() -> list[DiscoveredSkill]:
    """skill_packages_root() 中的系统包。"""

    items: list[DiscoveredSkill] = []
    root = skill_packages_root()
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
    for directory_name in SKILL_SCAN_DIR_NAMES:
        for skill_md in sorted((root / directory_name).glob("*/SKILL.md")) if (root / directory_name).is_dir() else []:
            item = _discovered_from_package(
                skill_md.parent.absolute(),
                enabled=True,
                source_kind="external_agents",
                source_scope="project",
                stable_id=skill_md.parent.name,
            )
            if item is None:
                continue
            refs.add(item.id)
            refs.add(item.name)
    return refs


def register_skill_scan_root(path: Path) -> None:
    """扩展口：把额外目录纳入 Skill 扫描。调用方负责传入已经约定好的根。"""

    resolved = Path(path)
    if resolved not in _extra_skill_roots:
        _extra_skill_roots.append(resolved)


def clear_extra_skill_scan_roots() -> None:
    """测试隔离用，清掉扩展扫描根。"""

    _extra_skill_roots.clear()


def skill_scan_targets(
    project_roots: list[Path],
    *,
    include_runtime: bool = True,
    runtime_root: Path | None = None,
) -> list[tuple[Path, str, bool, str]]:
    """返回要扫描的目录。runtime 是 agent-workstation/skills，其余是 .agent(s)/skills 这类目录。"""

    targets: list[tuple[Path, str, bool, str]] = []
    if include_runtime:
        targets.append((Path(runtime_root) if runtime_root is not None else skill_packages_root(), "runtime", True, "runtime"))
    bases: list[tuple[Path, str, bool, str]] = [(tenant_root(), "user", False, "external_agents")]
    for root in project_roots:
        bases.append((Path(root), "project", True, "external_agents"))
    for base, scope, enabled, kind in bases:
        for name in SKILL_SCAN_DIR_NAMES:
            targets.append((base / name, scope, enabled, kind))
    for extra in _extra_skill_roots:
        targets.append((Path(extra), "extra", True, "extension"))
    return targets


def scan_skill_directories(
    targets: list[tuple[Path, str, bool, str]],
) -> list[DiscoveredSkill]:
    """扫描给定根目录下一层的 SKILL.md。后出现的同名包遮蔽先出现的，被遮蔽项带 shadowed_by。"""

    grouped: dict[str, list[DiscoveredSkill]] = {}
    order: list[str] = []
    for root, scope, enabled, source_kind in targets:
        try:
            resolved = Path(root).resolve()
        except OSError:
            continue
        if not resolved.is_dir():
            continue
        for skill_md in sorted(resolved.glob("*/SKILL.md")):
            package_dir = skill_md.parent
            try:
                if not package_dir.resolve().is_relative_to(resolved):
                    continue
            except (OSError, ValueError):
                continue
            item = _discovered_from_package(
                package_dir,
                enabled=enabled,
                source_kind=source_kind,
                source_scope=scope,
                stable_id=package_dir.name,
            )
            if item is None:
                continue
            if item.id not in grouped:
                order.append(item.id)
                grouped[item.id] = []
            grouped[item.id].append(item)
    result: list[DiscoveredSkill] = []
    for skill_id in order:
        items = grouped[skill_id]
        winner = items[-1]
        winner_ref = winner.source_path or winner.resource_path
        for item in items[:-1]:
            result.append(replace(item, shadowed_by=winner_ref))
        result.append(winner)
    return result
