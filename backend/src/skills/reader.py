"""已解析 Skill 的唯一正文读取入口，不按名称二次选包。"""
from __future__ import annotations

import hashlib

from src.skills.models import ResolvedSkill, SkillError, SkillRead
from src.skills.parser import MAX_MANUAL_BYTES, parse_manual
from src.skills.policy import read_policy


def read_skill(resolved: ResolvedSkill, expected_hash: str | None = None) -> SkillRead:
    """核对定位、当前策略和原始 hash；不使用旧数据库/进程正文回退。"""

    candidate = resolved.candidate
    source = candidate.source_path
    try:
        if source.resolve() != source or not source.resolve().is_relative_to(candidate.root.path.resolve()):
            raise SkillError("skill_source_changed", "Skill 来源发生变化")
        with source.open("rb") as stream:
            raw = stream.read(MAX_MANUAL_BYTES + 1)
    except FileNotFoundError as exc:
        raise SkillError("skill_missing", f"Skill 文件已移除：{candidate.name}") from exc
    except (OSError, RuntimeError) as exc:
        raise SkillError("skill_unreadable", f"无法读取 Skill：{candidate.name}") from exc
    policy = read_policy(resolved.policy_path)
    if policy.version != resolved.policy_version:
        raise SkillError("skill_catalog_stale", "Skill 策略已更新，请刷新目录")
    actual = hashlib.sha256(raw).hexdigest()
    if expected_hash is not None and actual != expected_hash:
        raise SkillError("skill_version_changed", f"Skill 正文已变更，请重新确认：{candidate.name}")
    from src.skills.catalog import _resources
    try:
        resources = _resources(candidate.package_path)
    except (OSError, SkillError) as exc:
        raise SkillError("skill_catalog_stale", "Skill 支持文件来源发生变化，请刷新目录") from exc
    if resources != candidate.resources and candidate.root.kind != "explicit":
        raise SkillError("skill_catalog_stale", "Skill 支持文件列表发生变化，请刷新目录")
    if actual != candidate.content_hash:
        raise SkillError("skill_catalog_stale", f"Skill 正文已更新，请刷新目录：{candidate.name}")
    parsed = parse_manual(raw, candidate.package_path.name)
    return SkillRead(candidate=candidate, markdown=parsed.markdown, content_hash=actual, resources=candidate.resources)
