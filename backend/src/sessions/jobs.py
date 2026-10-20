"""Job 必須有 owner Session。沒有來源時建立 job-owner Session。"""

from __future__ import annotations

from src.sessions.records import create_session, get_session


def assign_job_owner(job_id: str, created_from_session_id: str = "", *, title: str = "") -> str:
    """寫入 owner_session_id。不建立沒有 Session 的 Job。"""

    owner = created_from_session_id if created_from_session_id and get_session(created_from_session_id) else ""
    if not owner:
        from src.storage.database import get_scheduled_job

        job = get_scheduled_job(job_id) or {}
        owner = create_session(
            title=title or "定時任務",
            kind="job-owner",
            agent_id=str(job.get("agent_id") or "default"),
            project_id=str(job.get("project_id") or ""),
        )["id"]
    from src.storage import database

    database.ensure_db()
    with database._lock, database._connect() as conn:
        conn.execute(
            "UPDATE scheduled_jobs SET owner_session_id = ? WHERE id = ?",
            (owner, job_id),
        )
        conn.commit()
    return owner
