"""8.6–8.8：firing 目錄、刪除與 ticker、舊 Job owner。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.config.runtime_paths import session_root
from src.sandbox.session_mounts import resolve_sandbox_layout
from src.scheduler.job_workspace import resolve_job_workspace
from src.scheduler.legacy_migration import migrate_legacy_jobs
from src.scheduler.models import ScheduledJobStateError
from src.scheduler.service import ScheduledJobService
from src.scheduler.ticker import JobScheduler
from src.sessions.errors import SessionError
from src.sessions.records import create_session, get_session
from src.sessions.service import hard_delete_session
from src.storage import database
from src.storage.database import (
    DEFAULT_PROJECT_ID,
    DEFAULT_WORKSPACE_ID,
    insert_scheduled_job,
    insert_scheduled_job_run,
    list_scheduled_job_runs,
    update_scheduled_job,
    update_scheduled_job_run,
)
from src.tenancy.context import TenantContext, use_tenant

HOSTED = "usr_" + "ab" * 16


def _job(job_id: str, **overrides) -> None:
    database.ensure_db()
    now = "2026-08-21T00:00:00+00:00"
    payload = {
        "id": job_id,
        "name": job_id,
        "status": "paused",
        "agent_id": "default",
        "project_id": DEFAULT_PROJECT_ID,
        "workspace_id": DEFAULT_WORKSPACE_ID,
        "schedule_kind": "weekly",
        "schedule_expr": {"weekdays": ["fri"], "at": "18:00"},
        "timezone": "Asia/Shanghai",
        "frozen_prompt": "寫日報",
        "created_at": now,
        "updated_at": now,
    }
    payload.update(overrides)
    insert_scheduled_job(payload)


def test_agent_and_script_workspaces_live_under_session_firings():
    session = create_session(title="owner", kind="job-owner", project_id=DEFAULT_PROJECT_ID)
    agent_dir = resolve_job_workspace(session["id"], "job-agent", "firing-agent")
    script_dir = resolve_job_workspace(session["id"], "job-script", "firing-script")
    assert agent_dir.parent.name == "firings"
    assert script_dir.parent.name == "firings"
    assert agent_dir.is_relative_to(session_root(session["id"]))
    assert (agent_dir / "outputs").is_dir()
    assert ".agent-workstation" not in agent_dir.as_posix()


def test_windows_job_path_is_rejected():
    session = create_session(title="owner", kind="job-owner")
    with pytest.raises(ValueError, match="路徑不合法"):
        resolve_job_workspace(session["id"], r"C:\Windows\job", "firing-1")
    with pytest.raises(ValueError, match="路徑不合法"):
        resolve_job_workspace(session["id"], "job-1", r"..\escape")


def test_hosted_firing_mount_stays_inside_tenant():
    with use_tenant(TenantContext.hosted(HOSTED)):
        session = create_session(title="hosted", kind="job-owner")
        workspace = resolve_job_workspace(session["id"], "job-hosted", "firing-hosted")
        layout = resolve_sandbox_layout({
            "sessionId": session["id"],
            "workingDirectory": str(workspace),
            "allowedRoots": [{
                "normalizedPath": str(workspace),
                "accessMode": "read_write",
            }],
        })
        boundary = TenantContext.hosted(HOSTED).data_root.resolve()
        assert workspace.resolve().is_relative_to(boundary)
        assert all(
            __import__("pathlib").Path(item["source"]).resolve().is_relative_to(boundary)
            for item in layout.mounts
        )
        with pytest.raises(SessionError, match="tenant"):
            resolve_sandbox_layout({
                "sessionId": session["id"],
                "workingDirectory": "/etc",
                "allowedRoots": [{"normalizedPath": "/etc", "accessMode": "read_write"}],
            })


def test_delete_blocks_active_manual_and_due_firings_until_restart_reclaim():
    owner = create_session(title="owner", kind="job-owner", project_id=DEFAULT_PROJECT_ID)
    _job("job-live", status="active", owner_session_id="")
    from src.sessions.jobs import assign_job_owner

    assign_job_owner("job-live", owner["id"])
    with pytest.raises(SessionError, match="active Job"):
        hard_delete_session(owner["id"])

    service = ScheduledJobService()
    service.run_now("job-live")
    update_scheduled_job("job-live", {"status": "paused"})
    with pytest.raises(SessionError, match="active Job"):
        hard_delete_session(owner["id"])

    update_scheduled_job_run(service.list_firings("job-live")[0].id, {"status": "succeeded"})
    due = insert_scheduled_job_run({
        "id": "firing-due",
        "job_id": "job-live",
        "trigger": "schedule",
        "scheduled_for": "2026-08-21T10:00:00+00:00",
        "status": "queued",
    })
    with pytest.raises(SessionError, match="active Job"):
        hard_delete_session(owner["id"])

    service.mark_starting(due["id"])
    update_scheduled_job_run(due["id"], {"started_at": "2020-01-01T00:00:00+00:00"})
    assert JobScheduler(service=service, stale_seconds=1).reclaim_orphan_starting(
        now=datetime.now(timezone.utc),
    ) == 1
    hard_delete_session(owner["id"])
    assert get_session(owner["id"]) is None


def test_delete_rechecks_lease_when_firing_appears_before_commit():
    owner = create_session(title="owner", kind="job-owner", project_id=DEFAULT_PROJECT_ID)
    _job("job-race", owner_session_id="")
    from src.sessions.jobs import assign_job_owner

    assign_job_owner("job-race", owner["id"])

    def appear(conn):
        conn.execute(
            """
            INSERT INTO scheduled_job_runs (
                id, job_id, workspace_id, project_id, trigger, scheduled_for, status,
                chat_session_id, chat_run_id, error_code, error_message, output_manifest,
                delivery_status, created_at, updated_at
            ) VALUES (
                'firing-race', 'job-race', ?, ?, 'manual', '2026-08-21T11:00:00+00:00',
                'queued', '', '', '', '', '{}', 'not_applicable',
                '2026-08-21T11:00:00+00:00', '2026-08-21T11:00:00+00:00'
            )
            """,
            (DEFAULT_WORKSPACE_ID, DEFAULT_PROJECT_ID),
        )

    with pytest.raises(SessionError, match="active Job"):
        hard_delete_session(owner["id"], before_commit=appear)
    assert get_session(owner["id"]) is not None


def test_due_dispatch_skips_deleted_owner_session():
    owner = create_session(title="owner", kind="job-owner", project_id=DEFAULT_PROJECT_ID)
    _job(
        "job-due",
        status="paused",
        next_run_at="2020-01-01T00:00:00+00:00",
    )
    from src.sessions.jobs import assign_job_owner

    assign_job_owner("job-due", owner["id"])
    hard_delete_session(owner["id"])
    update_scheduled_job("job-due", {"status": "active"})
    service = ScheduledJobService()
    stats = {"skipped_in_flight": 0, "claimed": 0, "missed": 0, "paused_daily_cap": 0}
    JobScheduler(service=service)._dispatch_due_job(
        service.get_job("job-due"),
        now=datetime.now(timezone.utc) + timedelta(days=1),
        stats=stats,
    )
    assert stats["skipped_in_flight"] == 1
    assert list_scheduled_job_runs("job-due") == []
    with pytest.raises(ScheduledJobStateError, match="owner Session"):
        service.run_now("job-due")


def test_legacy_jobs_keep_history_and_do_not_expand_skills():
    chat = create_session(title="來源", kind="chat", project_id=DEFAULT_PROJECT_ID)
    _job(
        "job-from-chat",
        created_from_session_id=chat["id"],
        skill_ids=["only-this"],
        required_skills=[{"skillId": "only-this", "contentSha256": "abc"}],
    )
    _job("job-orphan", skill_ids=["missing-skill", "also-missing"])
    insert_scheduled_job_run({
        "id": "firing-old",
        "job_id": "job-from-chat",
        "trigger": "schedule",
        "scheduled_for": "2026-01-01T00:00:00+00:00",
        "status": "succeeded",
        "output_manifest": {"resultText": "歷史結果"},
    })
    before = list_scheduled_job_runs("job-from-chat")
    report = migrate_legacy_jobs()
    owners = {item["jobId"]: item for item in report["migrated"]}
    assert owners["job-from-chat"]["ownerSessionId"] == chat["id"]
    assert owners["job-from-chat"]["source"] == "created_from"
    assert owners["job-orphan"]["source"] == "job-owner"
    assert get_session(owners["job-orphan"]["ownerSessionId"])["kind"] == "job-owner"
    assert list_scheduled_job_runs("job-from-chat") == before
    stored = database.get_scheduled_job("job-orphan")
    assert [item["skillId"] if isinstance(item, dict) else item for item in stored["required_skills"]] == [
        "missing-skill",
        "also-missing",
    ]
    assert stored["skill_ids"] == ["missing-skill", "also-missing"]
    kept = database.get_scheduled_job("job-from-chat")
    assert kept["required_skills"][0]["contentSha256"] == "abc"
