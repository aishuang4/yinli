"""把尚未綁定 owner 的舊 Job 收進 Session。不改歷史 firing，不擴大 Skill。"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from src.sessions.jobs import assign_job_owner
from src.storage.database import list_scheduled_job_runs, list_scheduled_jobs, update_scheduled_job


def migrate_legacy_jobs() -> dict[str, Any]:
    """優先沿用 created_from_session_id；否則建立 job-owner。只凍結已寫明的 Skill。"""

    migrated: list[dict[str, str]] = []
    for record in list_scheduled_jobs():
        if str(record.get("owner_session_id") or ""):
            continue
        job_id = str(record["id"])
        before_runs = list_scheduled_job_runs(job_id)
        created_from = str(record.get("created_from_session_id") or "")
        owner = assign_job_owner(job_id, created_from, title=str(record.get("name") or "定時任務"))
        skills = _freeze_explicit_skills(record)
        if skills is not None:
            update_scheduled_job(job_id, {"required_skills": skills, "skill_ids": list(record.get("skill_ids") or [])})
        after_runs = list_scheduled_job_runs(job_id)
        if before_runs != after_runs:
            raise RuntimeError(f"遷移改寫了歷史 firing: {job_id}")
        migrated.append({
            "jobId": job_id,
            "ownerSessionId": owner,
            "source": "created_from" if created_from else "job-owner",
        })
    return {"migrated": migrated}


def _freeze_explicit_skills(record: dict[str, Any]) -> list[dict[str, Any]] | None:
    existing = list(record.get("required_skills") or [])
    if existing and all(str(item.get("contentSha256") or item.get("content_sha256") or "") for item in existing):
        return None
    skill_ids = [str(item) for item in list(record.get("skill_ids") or []) if str(item)]
    if not skill_ids:
        return None
    frozen: list[dict[str, Any]] = []
    for skill_id in skill_ids:
        item = _hash_named_skill(skill_id)
        if item is None:
            frozen.append({
                "skillId": skill_id,
                "contentSha256": "",
                "sourceScope": "missing",
            })
            continue
        frozen.append(item)
    return frozen


def _hash_named_skill(skill_id: str) -> dict[str, Any] | None:
    from src.skills import SkillError, resolve_skill
    from src.skills.service import catalog_for

    try:
        resolved = resolve_skill(skill_id, catalog_for(), require_enabled=False)
    except SkillError:
        return None
    candidate = resolved.candidate
    digest = str(candidate.content_hash or "")
    source = Path(candidate.source_path)
    if not digest and source.is_file():
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
    return {
        "skillId": candidate.id,
        "loadName": candidate.name,
        "sourcePath": str(source),
        "sourceScope": "global",
        "contentSha256": digest,
    }
