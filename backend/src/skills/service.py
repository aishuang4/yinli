"""Skill 应用门面：上下文定位、API 投影和原子受管包发布。"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from src.skills.api_models import Skill, SkillRegisterRequest
from src.skills.catalog import build_skill_catalog, current_skill_context, resolve_skill
from src.skills.models import SkillCandidate, SkillCatalogSnapshot, SkillContext, SkillError
from src.skills.parser import parse_manual
from src.skills.policy import set_skill_policy, policy_transaction
from src.skills.reader import read_skill


def catalog_for(*, refresh=False):
    """目前 tenant 的 Skill 目錄。不再按 Workspace 或 Project 裁剪。"""

    return build_skill_catalog(current_skill_context(), refresh=refresh)


def skill_from_candidate(item: SkillCandidate, catalog: SkillCatalogSnapshot, *, include_body=False) -> Skill:
    """列表不返回正文；运行时对象携带同一已解析目标供 viewer 使用。"""

    resolved = None
    content = ""
    if item.state != "invalid":
        resolved = resolve_skill(item.id, catalog, require_enabled=False)
        if include_body:
            content = read_skill(resolved).markdown
    skill = Skill(id=item.id, name=item.name, description=item.description, category=item.category,
                  trigger=item.trigger, content=content, tags=list(item.tags), resource_path=str(item.package_path),
                  resources=list(item.resources), status="error" if item.state == "invalid" else ("installed" if item.installed else "available"),
                  enabled=item.enabled and item.state != "invalid", available_to_all_agents=item.shared,
                  updated_at=datetime.fromtimestamp(item.source_path.stat().st_mtime, timezone.utc).isoformat() if item.source_path.exists() else "", workspace_id=catalog.context.workspace_id, source_kind=item.root.kind,
                  source_scope=item.root.scope, source_path=str(item.source_path), content_hash=item.content_hash,
                  load_name=item.load_name, root_key=item.root.key, owner_key=item.root.owner_key,
                  resolution_state=item.state, error_code=item.error_code, reason=item.reason,
                  shadowed_by=list(item.shadowed_by), managed=item.root.managed and item.root.kind != "system",
                  alternatives=[other.load_name for other in catalog.candidates if other.name == item.name and other.id != item.id],
                  catalog_version=catalog.signature)
    from src.skills.packages import _skill_package_warnings
    skill.warnings = _skill_package_warnings(name=item.name, skill_path=str(item.source_path), resources=list(item.resources))
    skill._resolved_skill = resolved
    skill._catalog_context = catalog.context
    skill._catalog_snapshot = catalog
    return skill


def list_catalog_skills(catalog: SkillCatalogSnapshot, *, include_body=False) -> list[Skill]:
    """管理和运行目录只返回真实文件包；扫描诊断保留在 catalog 快照中。"""

    return [skill_from_candidate(item, catalog, include_body=include_body) for item in catalog.candidates]


def get_catalog_skill(ref: str, catalog: SkillCatalogSnapshot, *, require_enabled=False, include_body=True) -> Skill:
    selected = resolve_skill(ref, catalog, require_enabled=require_enabled)
    return skill_from_candidate(selected.candidate, catalog, include_body=include_body)


def registration_markdown(data: SkillRegisterRequest) -> str:
    """普通正文补齐标准 frontmatter，已有说明书采用唯一 parser 验证。"""

    import yaml
    body = data.content.strip()
    if body.lstrip("\ufeff").startswith("---"):
        parse_manual(body.encode(), data.name)
        return body
    metadata = {"name": data.name, "description": data.description or f"用户注册的 Skill: {data.name}",
                "category": data.category, "trigger": data.trigger, "tags": data.tags[:8]}
    return f"---\n{yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False)}---\n\n{body or '请补充执行步骤。'}\n"


def publish_skill(context: SkillContext, name: str, markdown: str, *, source_directory: Path | None = None) -> Skill:
    """同一 Workspace 的发布串行；半包位于扫描排除目录，成功前不对外可见。"""

    from src.skills.catalog import SYSTEM_SKILL_IDS
    from src.skills.packages import _filename_slug
    parsed = parse_manual(markdown.encode(), name)
    if parsed.name in SYSTEM_SKILL_IDS or _filename_slug(name) in SYSTEM_SKILL_IDS:
        raise SkillError("skill_reserved_name", "系统 Skill 不能被用户注册")
    root = context.tenant_root / "skills"
    root.mkdir(parents=True, exist_ok=True)
    with policy_transaction(root / ".publish.json"):
        catalog = build_skill_catalog(context, refresh=True)
        target = root / _filename_slug(name)
        def normalized(value):
            return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", value.lower())
        if target.exists() or any(normalized(item.name) == normalized(parsed.name) and item.root.managed for item in catalog.candidates):
            raise SkillError("skill_exists", f"Skill 已存在：{parsed.name}")
        temp = Path(tempfile.mkdtemp(prefix=".publishing-", dir=root))
        published = False
        try:
            if source_directory:
                shutil.copytree(source_directory, temp, dirs_exist_ok=True, symlinks=False)
            (temp / "SKILL.md").write_text(markdown, encoding="utf-8")
            # 用与目录相同的支持文件边界检查，拒绝非法/越界文件。
            from src.skills.catalog import _resources
            _resources(temp)
            temp.rename(target)
            published = True
            catalog = build_skill_catalog(context, refresh=True)
            candidate = next((item for item in catalog.candidates if item.package_path == target.resolve()), None)
            if candidate is None:
                raise SkillError("skill_publish_failed", "Skill 包发布后无法从文件目录回读")
            set_skill_policy(context.policy_path, candidate.id, enabled=True, installed=True)
            return get_catalog_skill(candidate.id, build_skill_catalog(context, refresh=True))
        except Exception:
            if published:
                shutil.rmtree(target)
            raise
        finally:
            if temp.exists():
                shutil.rmtree(temp)
