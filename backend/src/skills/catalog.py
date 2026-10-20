"""Skill 文件目录与确定性引用解析；不查询 SQLite，也不导入 HTTP 路由。"""
from __future__ import annotations

import hashlib
import json
import os
from collections import OrderedDict
from pathlib import Path
from threading import RLock
from typing import Callable

from src.skills.models import (
    ResolvedSkill, SkillCandidate, SkillCatalogSnapshot, SkillContext,
    SkillDiagnostic, SkillError, SkillSourceRoot,
)
from src.skills.parser import MAX_MANUAL_BYTES, parse_manual
from src.skills.policy import SkillPolicy, read_policy

SYSTEM_SKILL_IDS = ("yinli-catalog", "yinli-charts", "word-documents")
SKIP_DIRS = frozenset((".git", "node_modules", ".venv", "venv", "__pycache__", "scripts", "references", "assets", "templates"))
MAX_DEPTH = 12
MAX_PACKAGES = 2048
MAX_ENTRIES = 20000
_cache: OrderedDict[str, SkillCatalogSnapshot] = OrderedDict()
_cache_lock = RLock()


def current_skill_context() -> SkillContext:
    """应用边界获取当前租户；纯扫描测试可直接传 SkillContext。"""

    from src.tenancy.context import current_tenant
    from src.config.runtime_paths import user_home_root
    tenant = current_tenant()
    return SkillContext(
        tenant_root=tenant.data_root,
        tenant_id=tenant.tenant_id,
        home_root=user_home_root(),
        hosted=tenant.is_hosted,
    )


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()).hexdigest()


def _canonical_path(path: Path) -> Path:
    """按实际目录项拼写归一，兼容大小写不敏感卷而不对所有路径 lower。"""
    resolved = path.expanduser().resolve()
    parts = resolved.parts
    current = Path(parts[0])
    for part in parts[1:]:
        candidate = current / part
        if current.is_dir() and candidate.exists():
            try:
                entries = list(current.iterdir())
                if not any(entry.name == part for entry in entries):
                    candidate = next((entry for entry in entries if entry.samefile(candidate)), candidate)
            except OSError:
                pass
        current = candidate
    return current


def stable_skill_id(root: SkillSourceRoot, relative_path: str) -> str:
    """位置决定身份；不对大小写敏感文件系统的路径擅自 lower。"""

    return "skill-" + _digest([root.owner_key, root.key, os.path.normcase(relative_path)])[:24]


def _inside(path: Path, root: Path) -> bool:
    return path.resolve().is_relative_to(root.resolve())


def build_skill_roots(context: SkillContext, policy: SkillPolicy) -> tuple[SkillSourceRoot, ...]:
    """只扫描应用统一 Skill 根；Project 不拥有独立 Skill 包目录。"""

    tenant = context.tenant_root.resolve()
    owner = f"tenant:{context.tenant_id}"
    roots: list[SkillSourceRoot] = []

    def add(key, path, priority, scope, kind, enabled=True, managed=False, owner_key=owner):
        path = _canonical_path(Path(path))
        if context.hosted and not _inside(path, tenant):
            raise SkillError("skill_scope_denied", "托管 Skill 来源必须位于当前租户目录")
        roots.append(SkillSourceRoot(key=key, owner_key=owner_key, path=path,
                                    priority=priority, scope=scope, kind=kind, enabled=enabled, managed=managed))

    add("tenant-packages", tenant / "skills", 0, "tenant", "managed", managed=True)
    result = []
    seen: set[Path] = set()
    for root in sorted(roots, key=lambda item: (item.priority, item.key)):
        if root.path not in seen:
            result.append(root)
            seen.add(root.path)
    return tuple(result)


def _resources(package: Path) -> tuple[str, ...]:
    """支持文件不跟随越界链接，遍历有界，参与目录版本签名。"""

    files: list[str] = []
    for base, dirs, names in os.walk(package, followlinks=False):
        relative = Path(base).relative_to(package)
        dirs[:] = sorted(d for d in dirs if d not in {".git", "node_modules", ".venv", "__pycache__"}
                         and not (Path(base) / d).is_symlink())
        if len(relative.parts) >= MAX_DEPTH:
            if dirs:
                raise SkillError("skill_scan_limit", "Skill 支持文件目录超过深度限制")
            dirs[:] = []
        for name in sorted(names):
            file = Path(base) / name
            if not _inside(file, package):
                raise SkillError("skill_scope_denied", "Skill 支持文件链接越过包边界")
            if file.is_file():
                files.append(file.relative_to(package).as_posix())
                if len(files) > MAX_ENTRIES:
                    raise SkillError("skill_scan_limit", "Skill 支持文件超过数量限制")
    return tuple(files)


def _scan_root(root: SkillSourceRoot, diagnostics: list[SkillDiagnostic]):
    """遇到说明书即停止下钻；符号链接只允许根内且去重。"""

    visited: set[Path] = set()
    pending = [(root.path, 0)]
    count = 0
    while pending:
        directory, depth = pending.pop()
        try:
            real = directory.resolve()
            if not real.is_relative_to(root.path):
                diagnostics.append(SkillDiagnostic(code="skill_scope_denied", message="Skill 链接超出来源根", source=str(directory)))
                continue
            if real in visited or not real.is_dir():
                continue
            visited.add(real)
            count += 1
            if count > MAX_ENTRIES:
                raise SkillError("skill_scan_limit", "Skill 根目录超过扫描数量限制")
            if (directory / "SKILL.md").is_file():
                yield directory, real
                continue
            children = sorted(directory.iterdir(), key=lambda path: path.name)
            for child in reversed(children):
                if child.name.startswith(".") or child.name in SKIP_DIRS or not child.is_dir():
                    continue
                if depth >= MAX_DEPTH:
                    diagnostics.append(SkillDiagnostic(code="skill_scan_limit", message="Skill 根目录超过扫描深度限制", source=str(child)))
                    continue
                pending.append((child, depth + 1))
        except (OSError, RuntimeError) as exc:
            diagnostics.append(SkillDiagnostic(code="skill_unreadable", message="无法扫描 Skill 目录", source=str(directory)))
        except SkillError as exc:
            diagnostics.append(SkillDiagnostic(code=exc.code, message=str(exc), source=str(directory)))
            return


def build_skill_catalog(context: SkillContext, *, refresh: bool = False) -> SkillCatalogSnapshot:
    """新 Run 重扫成员及原始字节；缓存只复用同签名不可变快照。"""

    policy = read_policy(context.policy_path)
    roots = build_skill_roots(context, policy)
    candidates: list[SkillCandidate] = []
    diagnostics: list[SkillDiagnostic] = [SkillDiagnostic.model_validate(item) for item in policy.migration.get("diagnostics", [])]
    seen: set[Path] = set()
    identities: dict[str, tuple] = {}
    signatures = []
    for root in roots:
        for entry, package in _scan_root(root, diagnostics):
            if package in seen:
                continue
            if len(candidates) >= MAX_PACKAGES:
                diagnostics.append(SkillDiagnostic(code="skill_scan_limit", message="Skill 包超过数量限制", source=str(root.path)))
                break
            seen.add(package)
            relative = package.relative_to(root.path).as_posix()
            system = root.key == "tenant-packages" and relative in SYSTEM_SKILL_IDS
            skill_id = relative if system else stable_skill_id(root, relative)
            identity = (root.owner_key, root.key, relative)
            if skill_id in identities and identities[skill_id] != identity:
                raise SkillError("skill_identity_collision", "Skill 稳定 ID 截短碰撞")
            identities[skill_id] = identity
            source = package / "SKILL.md"
            raw_hash = ""
            resources: tuple[str, ...] = ()
            name, description, trigger, category, tags = package.name, "", "", "外部导入", ()
            state, code, reason = "resolved", "", ""
            try:
                if not _inside(source, root.path):
                    raise SkillError("skill_scope_denied", "Skill 说明书链接超出来源根")
                with source.open("rb") as stream:
                    raw = stream.read(MAX_MANUAL_BYTES + 1)
                raw_hash = hashlib.sha256(raw).hexdigest()
                parsed = parse_manual(raw, package.name)
                name, description = parsed.name, parsed.description
                trigger = str(parsed.metadata.get("trigger") or description)
                category = str(parsed.metadata.get("category") or ("系统" if system else "外部导入"))
                tags = tuple(str(tag) for tag in parsed.metadata.get("tags", []) if isinstance(tag, str))
                resources = _resources(package)
                if not system and (name in SYSTEM_SKILL_IDS or package.name in SYSTEM_SKILL_IDS):
                    raise SkillError("skill_reserved_name", "该名称由系统 Skill 保留")
                signatures.append((skill_id, raw_hash, tuple((name, (package / name).stat().st_size,
                                                            (package / name).stat().st_mtime_ns,
                                                            (package / name).stat().st_ctime_ns) for name in resources)))
            except (OSError, RuntimeError) as exc:
                state, code, reason = "invalid", "skill_unreadable", "无法读取 Skill 包"
            except SkillError as exc:
                state, code, reason = "invalid", exc.code, str(exc)
            override = policy.overrides.get(skill_id)
            enabled = override.enabled if override and override.enabled is not None else root.enabled
            shared = override.shared if override and override.shared is not None else True
            installed = override.installed if override and override.installed is not None else True
            aliases = tuple(alias for alias, targets in policy.legacy_aliases.items() if skill_id in targets)
            candidates.append(SkillCandidate(id=skill_id, name=name, description=description, trigger=trigger,
                category=category, tags=tags, root=root.model_copy(update={"kind": "system", "scope": "system"}) if system else root,
                relative_path=relative, package_path=package, source_path=source, content_hash=raw_hash,
                resources=resources, load_name=skill_id if system else f"{root.key}:{relative}", enabled=enabled,
                shared=True if skill_id == "yinli-catalog" else shared, installed=installed,
                state=state, error_code=code, reason=reason, legacy_aliases=aliases))
    # 按主名称诊断；所有其他裸别名在 resolve_skill 中应用完全相同的层级规则。
    result = []
    for candidate in candidates:
        peers = [item for item in candidates if candidate.name in item.aliases and item.error_code != "skill_reserved_name"]
        highest = min((item.root.priority for item in peers), default=candidate.root.priority)
        winners = [item for item in peers if item.root.priority == highest]
        state = candidate.state
        shadowed_by: tuple[str, ...] = ()
        if state != "invalid":
            if candidate.root.priority > highest:
                state, shadowed_by = "shadowed", tuple(item.id for item in winners)
            elif len(winners) > 1:
                state = "ambiguous"
        result.append(candidate.model_copy(update={"state": state, "shadowed_by": shadowed_by}))
    current_ids = {item.id for item in result}
    for alias, targets in policy.legacy_aliases.items():
        if targets and not any(target in current_ids for target in targets):
            diagnostics.append(SkillDiagnostic(code="skill_missing", message="旧 Skill 引用的包文件缺失", reference=alias))
    signature = _digest([context.model_dump(mode="json"), policy.model_dump(mode="json"), signatures,
                         [item.model_dump(mode="json") for item in result], [item.model_dump() for item in diagnostics]])
    with _cache_lock:
        if not refresh and signature in _cache:
            _cache.move_to_end(signature)
            return _cache[signature]
        snapshot = SkillCatalogSnapshot(context=context, roots=roots, candidates=tuple(result),
                                        diagnostics=tuple(diagnostics), policy_version=policy.version, signature=signature)
        _cache[signature] = snapshot
        while len(_cache) > 32:
            _cache.popitem(last=False)
    return snapshot


def resolve_skill(reference: str, catalog: SkillCatalogSnapshot,
                  access: Callable[[SkillCandidate], bool] | None = None, *, require_enabled: bool = True) -> ResolvedSkill:
    """先选来源再授权，禁止高层停用/未授权后静默回落。"""

    reference = str(reference).strip()
    exact = [item for item in catalog.candidates if reference in (item.id, item.load_name)]
    legacy = [item for item in catalog.candidates if reference in item.legacy_aliases]
    exact = exact or legacy
    matches = exact or [item for item in catalog.candidates if reference in item.aliases]
    if not matches:
        raise SkillError("skill_missing", f"Skill 不存在：{reference}")
    if not exact:
        priority = min(item.root.priority for item in matches)
        matches = [item for item in matches if item.root.priority == priority]
    if len(matches) != 1:
        raise SkillError("skill_ambiguous", f"Skill 引用有歧义：{reference}", tuple(item.load_name for item in matches))
    candidate = matches[0]
    if candidate.state == "invalid":
        raise SkillError(candidate.error_code or "skill_invalid", candidate.reason)
    if require_enabled and (not candidate.installed or not candidate.enabled):
        raise SkillError("skill_disabled", f"Skill 已停用或卸载：{candidate.name}")
    if access is not None and not access(candidate):
        raise SkillError("skill_scope_denied", f"当前运行无权使用 Skill：{candidate.name}")
    return ResolvedSkill(candidate=candidate, catalog_signature=catalog.signature,
                         policy_path=catalog.context.policy_path, policy_version=catalog.policy_version)
