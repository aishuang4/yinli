"""定时 Job required Skill 的冻结、校验与 Context 元数据。

本模块只依赖 Skill registry、Project resource access 和 Job 数据模型，
不依赖 HTTP 路由，保证 scheduler 与 Chat Runtime 可以共享同一套资源判定。
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.catalog.skills import read_package_skill_bytes
from src.scheduler.models import JobRequiredSkill
from src.skills.registry import Skill, get_installed_skill, resolve_project_skill

SINGLE_SKILL_MAX_CHARS = 24_000
TOTAL_SKILL_MAX_CHARS = 48_000


class SkillManifestError(ValueError):
    """Skill 冻结或 firing 校验失败，携带稳定错误码。"""

    def __init__(self, code: str, message: str, *, skill_ids: Iterable[str] = ()) -> None:
        super().__init__(message)
        self.code = code
        self.skill_ids = tuple(str(item) for item in skill_ids if str(item))


@dataclass(frozen=True)
class LiveSkillContent:
    """当前包内 SKILL.md 的正文及版本信息。"""

    skill: Skill
    markdown: str
    content_sha256: str
    source_path: str
    source_scope: str
    content_chars: int
    estimated_tokens: int


def canonical_skill_ids(
    job_skill_ids: Iterable[Any] = (),
    contract_skill_ids: Iterable[Any] = (),
) -> list[str]:
    """Job 显式顺序优先，合同中的 Skill 追加，最终去重保序。"""

    result: list[str] = []
    seen: set[str] = set()
    for value in [*list(job_skill_ids or ()), *list(contract_skill_ids or ())]:
        skill_id = str(value or "").strip()
        if not skill_id or skill_id in seen:
            continue
        seen.add(skill_id)
        result.append(skill_id)
    return result


def _is_path_like_skill_ref(reference: str) -> bool:
    """绝对路径、家目录、Windows 路径或 SKILL.md 文件名都不是 Skill 引用。"""

    lowered = reference.lower()
    return (
        reference.startswith(("/", "~", "."))
        or "/" in reference
        or "\\" in reference
        or (len(reference) > 1 and reference[1] == ":" and reference[0].isalpha())
        or lowered.endswith("skill.md")
        or reference.endswith("/")
    )


def resolve_job_skill_refs(
    references: Iterable[Any],
    *,
    project_root: Path | None = None,
    workspace_id: str = "default",
) -> list[str]:
    """把 ScheduleJob.skill_ids 解析为全局稳定 Skill ID（去重保序）。

    只接受 catalog `<skill>` 条目中的 `<id>`；兼容期允许唯一名称 / 加载名并解析为 ID。
    路径一律拒绝（`skill_ref_invalid`），歧义名称返回 `skill_ambiguous` 并列出候选，
    不存在返回 `skill_missing`，绝不猜测或静默丢弃。
    """

    from src.skills import SkillError, resolve_skill
    from src.skills.service import catalog_for

    refs = canonical_skill_ids(references)
    if not refs:
        return []
    for ref in refs:
        if _is_path_like_skill_ref(ref):
            raise SkillManifestError(
                "skill_ref_invalid",
                f"skill_ids 只接受 Skill 目录中的稳定 ID，不能填写路径：{ref}",
                skill_ids=[ref],
            )
    catalog = catalog_for()
    resolved: list[str] = []
    for ref in refs:
        try:
            selected = resolve_skill(ref, catalog)
        except SkillError as exc:
            alternatives = f"（候选：{', '.join(exc.alternatives)}）" if exc.alternatives else ""
            raise SkillManifestError(exc.code, f"{exc}{alternatives}", skill_ids=[ref]) from exc
        resolved.append(selected.candidate.id)
    return canonical_skill_ids(resolved)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def read_live_skill_content(
    skill_id: str,
    *,
    project_root: Path | None = None,
    frozen_source_path: str = "",
    workspace_id: str = "default",
) -> LiveSkillContent:
    """从安装包原始字节读取正文并计算完整 SHA-256。"""

    from src.skills.service import catalog_for, get_catalog_skill
    from src.skills import resolve_skill, read_skill, SkillError
    try:
        catalog = catalog_for()
        selected = resolve_skill(skill_id, catalog)
        if frozen_source_path and Path(frozen_source_path).resolve() != selected.candidate.source_path:
            raise SkillError("skill_source_changed", "冻结 Skill 来源已发生变化")
        result = read_skill(selected)
        skill = get_catalog_skill(selected.candidate.id, catalog)
    except SkillError as exc:
        raise SkillManifestError(exc.code, str(exc), skill_ids=[skill_id]) from exc
    return LiveSkillContent(skill=skill, markdown=result.markdown, content_sha256=result.content_hash,
        source_path=str(result.candidate.source_path), source_scope=result.candidate.root.scope,
        content_chars=len(result.markdown), estimated_tokens=max(1, len(result.markdown.encode()) // 4))


def assert_project_skill_allowed(skill_id: str, project_resources: Any | None) -> None:
    """校验 Skill 在当前 Project 白名单内。"""

    if project_resources is None:
        return
    allowed = set(project_resources.filter_ids("skill", [skill_id]))
    if skill_id not in allowed:
        raise SkillManifestError(
            "skill_unbound",
            f"Skill 未绑定当前 Project: {skill_id}",
            skill_ids=[skill_id],
        )


def build_required_skill_manifest(
    *,
    job_skill_ids: Iterable[Any] = (),
    contract_skill_ids: Iterable[Any] = (),
    project_resources: Any | None = None,
    project_root: Path | None = None,
    frozen_at: str | None = None,
    workspace_id: str = "default",
) -> tuple[list[str], list[JobRequiredSkill], dict[str, LiveSkillContent]]:
    """解析 required Skill 并生成确认时冻结的 manifest。"""

    skill_ids = canonical_skill_ids(job_skill_ids, contract_skill_ids)
    if not skill_ids:
        return [], [], {}
    frozen_at = frozen_at or _utc_now()
    manifest: list[JobRequiredSkill] = []
    live: dict[str, LiveSkillContent] = {}
    total_chars = 0
    for skill_id in skill_ids:
        item = read_live_skill_content(skill_id, project_root=project_root,
                                       workspace_id=getattr(project_resources, "workspace_id", workspace_id))
        assert_project_skill_allowed(item.skill.id, project_resources)
        if item.content_chars > SINGLE_SKILL_MAX_CHARS:
            raise SkillManifestError(
                "skill_context_budget_exceeded",
                f"Skill {skill_id} 的 SKILL.md 超过单 Skill Context 上限 {SINGLE_SKILL_MAX_CHARS} 字符",
                skill_ids=[skill_id],
            )
        total_chars += item.content_chars
        if total_chars > TOTAL_SKILL_MAX_CHARS:
            raise SkillManifestError(
                "skill_context_budget_exceeded",
                f"required Skills 总正文超过 Context 上限 {TOTAL_SKILL_MAX_CHARS} 字符",
                skill_ids=skill_ids,
            )
        live[skill_id] = item
        manifest.append(JobRequiredSkill(
            skill_id=item.skill.id,
            load_name=item.skill.load_name,
            root_key=item.skill.root_key,
            owner_key=item.skill.owner_key,
            source_scope=item.source_scope,
            source_path=item.source_path,
            content_sha256=item.content_sha256,
            content_chars=item.content_chars,
            estimated_tokens=item.estimated_tokens,
            frozen_at=frozen_at,
        ))
    return list(dict.fromkeys(item.skill_id for item in manifest)), manifest, live


def validate_required_skill_manifest(
    manifest: Iterable[JobRequiredSkill | dict[str, Any]],
    *,
    canonical_ids: Iterable[Any] = (),
    project_resources: Any | None = None,
    project_root: Path | None = None,
    context_policy: str = "fresh",
    workspace_id: str = "default",
) -> tuple[list[Skill], dict[str, LiveSkillContent], dict[str, Any]]:
    """firing 前按冻结 hash 重新读取 Skill，返回 Runtime 对象和审计摘要。"""

    records = [
        item if isinstance(item, JobRequiredSkill) else JobRequiredSkill.model_validate(item)
        for item in list(manifest or [])
    ]
    from src.skills.service import catalog_for
    from src.skills import resolve_skill, SkillError
    catalog = catalog_for()
    def canonical(ref):
        try:
            return resolve_skill(ref, catalog, require_enabled=False).candidate.id
        except SkillError:
            return ref
    expected_ids = canonical_skill_ids([canonical(ref) for ref in canonical_ids], [canonical(item.skill_id) for item in records])
    by_id = {canonical(item.skill_id): item for item in records}
    if set(expected_ids) != set(by_id):
        missing = [ref for ref in expected_ids if ref not in by_id]
        raise SkillManifestError("skill_metadata_missing", f"Job required Skill 缺少冻结版本记录: {', '.join(missing)}", skill_ids=missing)
    skills: list[Skill] = []
    live: dict[str, LiveSkillContent] = {}
    summary_items: list[dict[str, Any]] = []
    total_chars = 0
    for skill_id in expected_ids:
        record = by_id[skill_id]
        current = read_live_skill_content(
            skill_id,
            project_root=project_root,
            frozen_source_path=record.source_path,
            workspace_id=getattr(project_resources, "workspace_id", workspace_id),
        )
        assert_project_skill_allowed(current.skill.id, project_resources)
        if record.root_key and record.root_key != current.skill.root_key:
            raise SkillManifestError("skill_source_changed", "冻结 Skill 来源根发生变化", skill_ids=[skill_id])
        if not record.content_sha256:
            raise SkillManifestError("skill_metadata_missing", "冻结 Skill 缺少正文 hash，请重新确认", skill_ids=[skill_id])
        if current.content_sha256 != record.content_sha256:
            raise SkillManifestError(
                "skill_version_changed",
                f"Skill {skill_id} 的 SKILL.md 已变化，请重新确认 Job",
                skill_ids=[skill_id],
            )
        if current.content_chars > SINGLE_SKILL_MAX_CHARS:
            raise SkillManifestError(
                "skill_context_budget_exceeded",
                f"Skill {skill_id} 超过单 Skill Context 上限",
                skill_ids=[skill_id],
            )
        total_chars += current.content_chars
        if total_chars > TOTAL_SKILL_MAX_CHARS:
            raise SkillManifestError(
                "skill_context_budget_exceeded",
                "required Skills 总正文超过 Context 上限",
                skill_ids=expected_ids,
            )
        skills.append(current.skill)
        live[skill_id] = current
        # 显式名称引用和 Runtime canonical ID 均可查到同一份已校验正文。
        live[current.skill.id] = current
        summary_items.append({
            "skillId": skill_id,
            "contentSha256": current.content_sha256,
            "contentChars": current.content_chars,
            "estimatedTokens": current.estimated_tokens,
            "sourceScope": current.source_scope,
            "sourcePath": current.source_path,
            "validated": True,
        })
    return skills, live, {
        "skillIds": expected_ids,
        "items": summary_items,
        "totalChars": total_chars,
        "policy": context_policy,
        "validation": "passed",
    }


def skill_context_audit_error(exc: SkillManifestError, *, policy: str = "fresh") -> dict[str, Any]:
    """构造不含正文的失败审计摘要。"""

    return {
        "skillIds": list(exc.skill_ids),
        "policy": policy,
        "validation": "failed",
        "errorCode": exc.code,
        "errorMessage": str(exc),
    }
