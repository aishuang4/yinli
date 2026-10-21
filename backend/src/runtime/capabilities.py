"""交互式與定時任務共用的能力裁決。

interactive 收的是呼叫端已經合併好的候選集，不再按 Project binding 過濾。
frozen 只認確認時寫下的記錄，按 source_path 重讀 SKILL.md 比對完整 sha256。
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

SKILL_HASH_CODES = frozenset({
    "skill_missing",
    "skill_moved",
    "skill_hash_changed",
    "skill_disabled",
    "skill_not_allowed",
    "skill_context_budget_exceeded",
})

SINGLE_SKILL_MAX_CHARS = 24_000
TOTAL_SKILL_MAX_CHARS = 48_000


class SkillManifestError(Exception):
    """凍結 Skill 無法按原包復現。code 會寫進 Job pause_reason。"""

    def __init__(self, code: str, skill_id: str = "") -> None:
        self.code = code
        self.skill_id = skill_id
        detail = f"{code}: {skill_id}" if skill_id else code
        super().__init__(detail)


@dataclass(frozen=True)
class CapabilitySet:
    """一輪 Run 最終可用的 Skill。"""

    skills: tuple[Any, ...] = ()
    warnings: tuple[str, ...] = ()
    required_skills: tuple[dict[str, Any], ...] = field(default_factory=tuple)


def resolve_capabilities(
    *,
    mode: Literal["interactive", "frozen"],
    agent: Any | None = None,
    requested_skill_ids: list[str] | None = None,
    candidates: list[Any] | None = None,
    frozen: list[dict[str, Any]] | None = None,
) -> CapabilitySet:
    """interactive 原樣保留候選；frozen 逐條讀檔校驗，失敗不降級。"""

    if mode == "frozen":
        return _resolve_frozen(agent=agent, frozen=list(frozen or []))
    skills = tuple(candidates or [])
    del requested_skill_ids
    warnings: list[str] = []
    if not skills:
        warnings.append("capability_set_empty")
        logger.warning("本輪 CapabilitySet 沒有 Skill")
    return CapabilitySet(skills=skills, warnings=tuple(warnings))


def freeze_job_skills(job: Any, *, agent: Any | None = None) -> list[dict[str, Any]]:
    """ConfirmJob 三源合併：顯式 skill_ids、toolkit required、合同 claimed。

    共享開關不會把額外 Skill 放進凍結列表。
    """

    if str(getattr(job, "mode", "") or "") == "script":
        return []
    explicit = [str(item) for item in list(getattr(job, "skill_ids", None) or []) if str(item).strip()]
    toolkit_ids = _required_toolkit_skill_ids(agent if agent is not None else _agent_of(job))
    contract = getattr(job, "capability_contract", None)
    claimed = [str(item) for item in list(getattr(contract, "skill_ids", None) or []) if str(item).strip()]
    ordered: list[str] = []
    origins: dict[str, str] = {}
    for skill_id, origin in (
        *((item, "explicit") for item in explicit),
        *((item, "required") for item in toolkit_ids),
        *((item, "claimed") for item in claimed),
    ):
        if skill_id in origins:
            continue
        origins[skill_id] = origin
        ordered.append(skill_id)
    if not ordered:
        logger.warning("確認 Job 時凍結 Skill 為空 job=%s", getattr(job, "id", ""))
        return []
    frozen_at = datetime.now(timezone.utc).isoformat()
    records: list[dict[str, Any]] = []
    total_chars = 0
    for skill_id in ordered:
        record = _freeze_one(skill_id, origin=origins[skill_id], frozen_at=frozen_at)
        total_chars += len(Path(record["source_path"]).read_text(encoding="utf-8"))
        if total_chars > TOTAL_SKILL_MAX_CHARS:
            raise SkillManifestError("skill_context_budget_exceeded", skill_id)
        records.append(record)
    return records


def _resolve_frozen(*, agent: Any | None, frozen: list[dict[str, Any]]) -> CapabilitySet:
    if not frozen:
        logger.warning("frozen CapabilitySet 為空")
        return CapabilitySet(warnings=("capability_set_empty",))
    from src.server.routes.skills import Skill

    ceiling = {str(item) for item in list(getattr(agent, "skills", None) or []) if str(item).strip()}
    toolkit_ids = set(_required_toolkit_skill_ids(agent))
    skills: list[Any] = []
    total_chars = 0
    for record in frozen:
        skill_id = str(record.get("id") or "")
        path = Path(str(record.get("source_path") or record.get("sourcePath") or ""))
        if ceiling and skill_id not in ceiling and skill_id not in toolkit_ids:
            raise SkillManifestError("skill_not_allowed", skill_id)
        if not path.is_file():
            moved = _installed_path(skill_id)
            code = "skill_moved" if moved and moved != path else "skill_missing"
            raise SkillManifestError(code, skill_id)
        text = path.read_text(encoding="utf-8")
        if len(text) > SINGLE_SKILL_MAX_CHARS:
            raise SkillManifestError("skill_context_budget_exceeded", skill_id)
        total_chars += len(text)
        if total_chars > TOTAL_SKILL_MAX_CHARS:
            raise SkillManifestError("skill_context_budget_exceeded", skill_id)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        expected = str(record.get("content_sha256") or record.get("contentSha256") or "")
        if expected and digest != expected:
            raise SkillManifestError("skill_hash_changed", skill_id)
        if not _still_enabled(skill_id):
            raise SkillManifestError("skill_disabled", skill_id)
        load_name, description = _frontmatter_identity(text, str(record.get("name") or skill_id))
        skills.append(Skill.model_validate({
            "id": skill_id,
            "name": load_name,
            "description": description,
            "category": "冻结",
            "content": text,
            "resourcePath": str(path.parent if path.name == "SKILL.md" else path),
            "status": "installed",
            "enabled": True,
            "updatedAt": str(record.get("frozen_at") or record.get("frozenAt") or ""),
        }))
    return CapabilitySet(skills=tuple(skills), required_skills=tuple(frozen))


def _freeze_one(skill_id: str, *, origin: str, frozen_at: str) -> dict[str, Any]:
    from src.server.routes.skills import get_installed_skill

    skill = get_installed_skill(skill_id)
    if skill is None:
        raise SkillManifestError("skill_missing", skill_id)
    if not bool(getattr(skill, "enabled", True)):
        raise SkillManifestError("skill_disabled", skill_id)
    source = str(getattr(skill, "resource_path", "") or getattr(skill, "source_path", "") or "")
    path = Path(source)
    skill_md = path / "SKILL.md" if path.is_dir() else path
    if not skill_md.is_file():
        raise SkillManifestError("skill_missing", skill_id)
    text = skill_md.read_text(encoding="utf-8")
    if len(text) > SINGLE_SKILL_MAX_CHARS:
        raise SkillManifestError("skill_context_budget_exceeded", skill_id)
    return {
        "id": skill_id,
        "name": str(getattr(skill, "name", "") or skill_id),
        "source_scope": str(getattr(skill, "source_scope", "") or ""),
        "source_path": str(skill_md),
        "content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "frozen_at": frozen_at,
        "origin": origin,
    }


def _frontmatter_identity(text: str, fallback: str) -> tuple[str, str]:
    """注入用的是 SKILL.md 的 name，不是目錄裡的包 id。"""

    from src.catalog.skills import frontmatter_identity

    return frontmatter_identity(text, fallback)


def _agent_of(job: Any) -> Any | None:
    from src.catalog.loader import get_registered_agent

    agent_id = str(getattr(job, "agent_id", "") or "")
    if not agent_id:
        return None
    return get_registered_agent(agent_id)


def _required_toolkit_skill_ids(agent: Any | None) -> list[str]:
    if agent is None:
        return []
    from src.scheduler.job_toolkit import job_toolkit_of

    return [item.id for item in job_toolkit_of(agent).required if item.kind == "skill" and item.id]


def _installed_path(skill_id: str) -> Path | None:
    from src.server.routes.skills import get_installed_skill

    skill = get_installed_skill(skill_id)
    if skill is None:
        return None
    source = str(getattr(skill, "resource_path", "") or "")
    if not source:
        return None
    path = Path(source)
    skill_md = path / "SKILL.md" if path.is_dir() else path
    return skill_md if skill_md.is_file() else None


def _still_enabled(skill_id: str) -> bool:
    """已安裝介面會把停用包濾掉，這裡直接看掃描快取。快取沒有這包時不擋檔案校驗。"""

    from src.skills.registry import _skills

    skill = _skills.get(skill_id)
    if skill is None:
        return True
    return bool(getattr(skill, "enabled", True)) and str(getattr(skill, "status", "")) == "installed"
