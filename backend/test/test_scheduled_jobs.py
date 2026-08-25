"""定时任务 schema、session kind、weekly/once 调度与状态机回归。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from src.agents.runtime_types import RuntimeToolScope
from src.scheduler.models import (
    DEFAULT_JOB_TOOL_SCOPE,
    DEFAULT_JOB_TOOL_SCOPE_JSON,
    JobToolScope,
    ScheduledJob,
    ScheduledJobRun,
)
from src.scheduler.schedule import (
    ScheduleError,
    compute_next_run_at,
    next_weekly_anchor_after,
    once_run_at,
    to_utc_iso,
    validate_schedule_expr,
)
from src.scheduler.service import ScheduledJobService
from src.scheduler.capability_contract import JobCapabilityContract
from src.scheduler.firing_toolkit import assemble_scheduled_firing_toolkit_spec
from src.scheduler.settings import is_scheduled_jobs_enabled
from src.server.routes.chat.models import ChatSession, ChatStore
from src.storage import database
from src.storage.database import (
    DEFAULT_PROJECT_ID,
    DEFAULT_WORKSPACE_ID,
    ensure_db,
    get_chat_run,
    list_user_notifications,
    load_chat_store,
    replace_skills,
    save_chat_store,
    upsert_agent,
    upsert_chat_run,
    upsert_project_resource_binding,
)
from src.storage.database_schema import BASE_SCHEMA_SQL


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    """每个用例使用独立 SQLite，避免污染开发库。"""

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "agent_workstation.db")
    monkeypatch.setattr(database, "_initialized", False)
    yield
    database._initialized = False


def _service() -> ScheduledJobService:
    ensure_db()
    _ensure_agent("agent-1")
    return ScheduledJobService()


def _ensure_agent(agent_id: str) -> None:
    upsert_agent({
        "id": agent_id,
        "name": agent_id,
        "system_prompt": "prompt",
        "workspaceId": DEFAULT_WORKSPACE_ID,
        "status": "idle",
    })


def _ensure_skill(skill_id: str) -> None:
    replace_skills({
        skill_id: {
            "id": skill_id,
            "name": skill_id,
            "description": "",
            "category": "test",
            "status": "installed",
            "enabled": True,
            "updatedAt": "2026-08-20T00:00:00+00:00",
            "workspaceId": DEFAULT_WORKSPACE_ID,
        },
    })
    upsert_project_resource_binding({
        "project_id": DEFAULT_PROJECT_ID,
        "resource_type": "skill",
        "resource_id": skill_id,
        "enabled": True,
    })


def _weekly_job(service: ScheduledJobService, **overrides) -> object:
    payload = {
        "workspace_id": DEFAULT_WORKSPACE_ID,
        "project_id": DEFAULT_PROJECT_ID,
        "name": "周五周报",
        "agent_id": "agent-1",
        "schedule_kind": "weekly",
        "schedule_expr": {"weekdays": ["fri"], "at": "18:00"},
        "timezone": "Asia/Shanghai",
        "frozen_prompt": "生成本周周报，写入 outputs/weekly-report.md",
    }
    payload.update(overrides)
    _ensure_agent(str(payload["agent_id"]))
    return service.create_job(**payload)


def test_default_job_tool_scope_is_all_false_not_runtime_tool_scope():
    """缺省冻结 scope 全 false，禁止落入 RuntimeToolScope 全 True。"""

    assert JobToolScope().model_dump() == DEFAULT_JOB_TOOL_SCOPE
    assert JobToolScope.model_validate({}).allow_bash is False
    assert JobToolScope.model_validate({}).allow_http is False
    runtime = RuntimeToolScope()
    assert runtime.allow_bash is True
    assert runtime.allow_mcp is True


def test_job_model_rejects_draft_and_retry_once():
    base = {
        "id": "job-1",
        "workspace_id": "default",
        "project_id": "default",
        "name": "x",
        "status": "pending_confirmation",
        "agent_id": "a",
        "schedule_kind": "weekly",
        "schedule_expr": {"weekdays": ["fri"], "at": "18:00"},
        "timezone": "Asia/Shanghai",
        "frozen_prompt": "do",
        "created_at": "2026-08-21T00:00:00+00:00",
        "updated_at": "2026-08-21T00:00:00+00:00",
    }
    with pytest.raises(ValidationError):
        ScheduledJob.model_validate({**base, "status": "draft"})
    with pytest.raises(ValidationError):
        ScheduledJob.model_validate({**base, "failure_policy": "retry_once"})
    with pytest.raises(ValidationError):
        ScheduledJob.model_validate({**base, "schedule_kind": "cron"})


def test_firing_status_includes_queued():
    firing = ScheduledJobRun.model_validate({
        "id": "firing-1",
        "job_id": "job-1",
        "workspace_id": "default",
        "project_id": "default",
        "trigger": "schedule",
        "scheduled_for": "2026-08-21T10:00:00+00:00",
        "status": "queued",
        "created_at": "2026-08-21T00:00:00+00:00",
        "updated_at": "2026-08-21T00:00:00+00:00",
    })
    assert firing.status == "queued"


def test_weekly_next_anchor_is_clock_face_not_plus_seven_days():
    after = datetime(2026, 8, 21, 10, 0, tzinfo=timezone.utc)  # 周五 18:00 上海
    nxt = next_weekly_anchor_after(
        after,
        weekdays=["fri"],
        at="18:00",
        timezone_name="Asia/Shanghai",
    )
    assert to_utc_iso(nxt) == "2026-08-28T10:00:00+00:00"


def test_weekly_dst_uses_local_clock_not_utc_plus_seven_days():
    """纽约夏令时切换后仍落在周五 18:00 本地，而不是 UTC+7 天。"""

    after = datetime(2026, 3, 6, 23, 0, tzinfo=timezone.utc)  # 周五 18:00 EST
    nxt = next_weekly_anchor_after(
        after,
        weekdays=["fri"],
        at="18:00",
        timezone_name="America/New_York",
    )
    assert to_utc_iso(nxt) == "2026-03-13T22:00:00+00:00"
    plus_seven = after + timedelta(days=7)
    assert to_utc_iso(nxt) != to_utc_iso(plus_seven)


def test_weekly_mixed_case_weekday_normalizes():
    after = datetime(2026, 8, 21, 10, 0, tzinfo=timezone.utc)
    nxt = next_weekly_anchor_after(
        after,
        weekdays=["Fri"],
        at="18:00",
        timezone_name="Asia/Shanghai",
    )
    assert to_utc_iso(nxt) == "2026-08-28T10:00:00+00:00"
    with pytest.raises(ScheduleError):
        next_weekly_anchor_after(
            after,
            weekdays=["Friday"],
            at="18:00",
            timezone_name="Asia/Shanghai",
        )


def test_weekly_before_same_day_clock_keeps_today():
    after = datetime(2026, 8, 21, 9, 0, tzinfo=timezone.utc)  # 周五 17:00 上海
    nxt = next_weekly_anchor_after(
        after,
        weekdays=["fri"],
        at="18:00",
        timezone_name="Asia/Shanghai",
    )
    assert to_utc_iso(nxt) == "2026-08-21T10:00:00+00:00"


def test_once_run_at_uses_timezone_column_without_offset():
    instant = once_run_at("2026-08-22T18:00:00", "Asia/Shanghai")
    assert to_utc_iso(instant) == "2026-08-22T10:00:00+00:00"
    with pytest.raises(ScheduleError):
        once_run_at("2026-08-22T18:00:00+08:00", "Asia/Shanghai")
    with pytest.raises(ScheduleError):
        validate_schedule_expr("cron", {"expr": "0 18 * * 5"})


def test_compute_next_run_at_once_and_weekly():
    weekly = compute_next_run_at(
        schedule_kind="weekly",
        schedule_expr={"weekdays": ["fri"], "at": "18:00"},
        timezone_name="Asia/Shanghai",
        after=datetime(2026, 8, 21, 10, 0, tzinfo=timezone.utc),
    )
    assert to_utc_iso(weekly) == "2026-08-28T10:00:00+00:00"
    once = compute_next_run_at(
        schedule_kind="once",
        schedule_expr={"at": "2026-08-22T18:00:00"},
        timezone_name="Asia/Shanghai",
        after=datetime(2026, 8, 21, 0, 0, tzinfo=timezone.utc),
    )
    assert to_utc_iso(once) == "2026-08-22T10:00:00+00:00"


def test_feature_flag_defaults_false(isolated_db):
    ensure_db()
    assert is_scheduled_jobs_enabled() is False


def test_create_job_writes_pending_confirmation_and_all_false_tool_scope(isolated_db):
    service = _service()
    job = _weekly_job(service)
    assert job.status == "pending_confirmation"
    assert job.status != "draft"
    assert job.project_id == DEFAULT_PROJECT_ID
    assert job.mode == "agent"
    assert job.tool_scope.model_dump() == DEFAULT_JOB_TOOL_SCOPE
    assert job.next_run_at is None
    assert job.capability_contract.verdict == "generate_only"


def test_capability_contract_roundtrip_and_empty_json(isolated_db):
    service = _service()
    contract = JobCapabilityContract.model_validate({
        "verdict": "ok",
        "taskIntent": "每周五发周报邮件",
        "requiredIntents": ["email.send", "file.write"],
        "claimed": [{
            "capabilityId": "mcp:gmail-send-email",
            "kind": "mcp",
            "resourceId": "gmail-send-email",
            "toolName": "send_email",
            "tags": ["email.send"],
            "unattendedOk": True,
        }],
    })
    job = _weekly_job(service, name="合同往返", capability_contract=contract)
    stored = service.get_job(job.id)
    assert stored.capability_contract.verdict == "ok"
    assert stored.capability_contract.task_intent == "每周五发周报邮件"
    assert stored.capability_contract.claimed[0].capability_id == "mcp:gmail-send-email"
    assert stored.tool_scope.allow_mcp is False
    empty = _weekly_job(service, name="空合同")
    assert empty.capability_contract.verdict == "generate_only"
    assert "capability_contract" in BASE_SCHEMA_SQL


def test_empty_contract_assemble_keeps_bash_ignores_dirty_mcp(isolated_db):
    service = _service()
    job = _weekly_job(service, name="脏五旗")
    service.update_job(job.id, {
        "tool_scope": {
            "allow_bash": True,
            "allow_mcp": True,
            "allow_http": True,
            "allow_web": True,
            "allow_knowledge": True,
        },
    })
    stored = service.get_job(job.id)
    spec = assemble_scheduled_firing_toolkit_spec(stored)
    assert spec["tool_scope"]["allow_bash"] is True
    assert spec["tool_scope"]["allow_mcp"] is False
    assert spec["legacy_tool_scope_ignored"] is True
    assert "FinalAnswerStart" in spec["firing_tools_preview"]
    assert "AskUser" not in spec["firing_tools_preview"]


def test_create_job_rejects_retry_once_and_duplicate_name(isolated_db):
    from src.scheduler.models import ScheduledJobConflictError, ScheduledJobValidationError

    service = _service()
    _weekly_job(service)
    with pytest.raises(ScheduledJobValidationError):
        _weekly_job(service, name="另一个", failure_policy="retry_once")
    with pytest.raises(ScheduledJobValidationError):
        _weekly_job(service, name="错过策略", missed_policy="retry_forever")
    with pytest.raises(ScheduledJobConflictError):
        _weekly_job(service, name="周五周报")
    recovered = _weekly_job(service, name="错过策略")
    assert recovered.missed_policy == "catch_up_once"


def test_status_transitions_confirm_pause_resume_archive(isolated_db):
    from src.scheduler.models import ScheduledJobStateError

    service = _service()
    job = _weekly_job(service)
    now = datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc)  # 周四
    active = service.confirm(job.id, now=now)
    assert active.status == "active"
    assert active.next_run_at == "2026-08-21T10:00:00+00:00"
    assert service.confirm(job.id, now=now).status == "active"

    paused = service.pause(job.id, reason="user")
    assert paused.status == "paused"
    assert paused.pause_reason == "user"

    resumed = service.resume(job.id, now=now)
    assert resumed.status == "active"
    assert resumed.next_run_at == "2026-08-21T10:00:00+00:00"

    archived = service.archive(job.id)
    assert archived.status == "archived"
    assert archived.archived_at is not None
    with pytest.raises(ScheduledJobStateError):
        service.confirm(job.id)


def test_confirm_rejects_auto_send(isolated_db):
    from src.scheduler.models import ScheduledJobValidationError

    service = _service()
    job = _weekly_job(service, delivery_policy="auto_send", name="自动发送")
    with pytest.raises(ScheduledJobValidationError):
        service.confirm(job.id)


def test_create_job_persists_canonical_weekday_and_finalize_accepts_fri(isolated_db):
    service = _service()
    job = _weekly_job(
        service,
        name="大小写周五",
        schedule_expr={"weekdays": ["Fri"], "at": "18:00"},
    )
    assert job.schedule_expr["weekdays"] == ["fri"]
    confirmed = service.confirm(
        job.id,
        now=datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc),
    )
    firing = service.create_firing(
        job_id=confirmed.id,
        scheduled_for=confirmed.next_run_at or "",
        trigger="schedule",
    )
    updated = service.complete_firing(firing.id, "succeeded")
    assert updated.status == "active"
    assert updated.next_run_at == "2026-08-28T10:00:00+00:00"


def test_finalize_job_clock_weekly_advances_from_scheduled_for(isolated_db):
    service = _service()
    job = service.confirm(
        _weekly_job(service).id,
        now=datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc),
    )
    firing = service.create_firing(
        job_id=job.id,
        scheduled_for="2026-08-21T10:00:00+00:00",
        trigger="schedule",
        status="queued",
    )
    assert firing.status == "queued"
    assert firing.project_id == DEFAULT_PROJECT_ID
    updated = service.complete_firing(firing.id, "succeeded")
    assert updated.status == "active"
    assert updated.next_run_at == "2026-08-28T10:00:00+00:00"
    # 再次 finalize 必须幂等，不能再跳一周
    again = service.finalize_job_clock(job.id, firing.id)
    assert again.next_run_at == "2026-08-28T10:00:00+00:00"


def test_finalize_job_clock_weekly_pause_job_on_failure(isolated_db):
    service = _service()
    job = service.confirm(
        _weekly_job(service, failure_policy="pause_job", name="失败暂停").id,
        now=datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc),
    )
    firing = service.create_firing(
        job_id=job.id,
        scheduled_for=job.next_run_at or "",
        trigger="schedule",
    )
    updated = service.complete_firing(firing.id, "failed", error_code="timeout")
    assert updated.status == "paused"
    assert updated.pause_reason == "failure_policy"
    assert updated.next_run_at == "2026-08-28T10:00:00+00:00"


def test_finalize_job_clock_weekly_notify_keep_schedule_stays_active(isolated_db):
    service = _service()
    job = service.confirm(
        _weekly_job(service, name="失败继续").id,
        now=datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc),
    )
    firing = service.create_firing(
        job_id=job.id,
        scheduled_for=job.next_run_at or "",
        trigger="schedule",
    )
    updated = service.complete_firing(firing.id, "failed", error_code="timeout")
    assert updated.status == "active"
    assert updated.next_run_at == "2026-08-28T10:00:00+00:00"
    assert updated.next_run_at != firing.scheduled_for


def test_finalize_job_clock_once_success_and_failure(isolated_db):
    service = _service()
    success = service.create_job(
        workspace_id=DEFAULT_WORKSPACE_ID,
        project_id=DEFAULT_PROJECT_ID,
        name="一次性成功",
        agent_id="agent-1",
        schedule_kind="once",
        schedule_expr={"at": "2026-08-22T18:00:00"},
        timezone="Asia/Shanghai",
        frozen_prompt="写一份纪要",
    )
    success = service.confirm(success.id)
    assert success.next_run_at == "2026-08-22T10:00:00+00:00"
    firing = service.create_firing(
        job_id=success.id,
        scheduled_for=success.next_run_at or "",
        trigger="schedule",
    )
    completed = service.complete_firing(firing.id, "succeeded")
    assert completed.status == "completed"
    assert completed.next_run_at is None

    failed = service.create_job(
        workspace_id=DEFAULT_WORKSPACE_ID,
        project_id=DEFAULT_PROJECT_ID,
        name="一次性失败",
        agent_id="agent-1",
        schedule_kind="once",
        schedule_expr={"at": "2026-08-22T18:00:00"},
        timezone="Asia/Shanghai",
        frozen_prompt="写一份纪要",
    )
    failed = service.confirm(failed.id)
    miss = service.create_firing(
        job_id=failed.id,
        scheduled_for=failed.next_run_at or "",
        trigger="schedule",
        status="queued",
    )
    paused = service.complete_firing(miss.id, "missed", error_code="missed")
    assert paused.status == "paused"
    assert paused.pause_reason == "once_failed"
    assert paused.next_run_at is None


def test_firing_unique_constraint_and_queued_not_stale(isolated_db):
    from src.scheduler.models import ScheduledJobConflictError

    service = _service()
    job = service.confirm(
        _weekly_job(service).id,
        now=datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc),
    )
    scheduled_for = job.next_run_at or ""
    first = service.create_firing(
        job_id=job.id,
        scheduled_for=scheduled_for,
        trigger="schedule",
        status="queued",
    )
    assert first.status == "queued"
    assert service.has_non_terminal_firing(job.id) is True
    with pytest.raises(ScheduledJobConflictError):
        service.create_firing(
            job_id=job.id,
            scheduled_for=scheduled_for,
            trigger="schedule",
        )
    with pytest.raises(ScheduledJobConflictError):
        service.create_firing(
            job_id=job.id,
            scheduled_for=scheduled_for,
            trigger="manual",
        )
    completed = service.complete_firing(first.id, "succeeded")
    assert completed.next_run_at == "2026-08-28T10:00:00+00:00"
    with pytest.raises(ScheduledJobConflictError):
        service.create_firing(
            job_id=job.id,
            scheduled_for=scheduled_for,
            trigger="schedule",
        )
    period_next = completed.next_run_at
    manual = service.create_firing(
        job_id=job.id,
        scheduled_for=to_utc_iso(datetime.now(timezone.utc)),
        trigger="manual",
    )
    assert manual.trigger == "manual"
    after_manual = service.complete_firing(manual.id, "succeeded")
    assert after_manual.next_run_at == period_next


def test_catch_up_clock_uses_now_when_later_than_next_face(isolated_db):
    service = _service()
    job = service.confirm(
        _weekly_job(
            service,
            name="周一周五",
            schedule_expr={"weekdays": ["mon", "fri"], "at": "18:00"},
        ).id,
        now=datetime(2026, 8, 16, 0, 0, tzinfo=timezone.utc),
    )
    monday = "2026-08-17T10:00:00+00:00"
    firing = service.create_firing(
        job_id=job.id,
        scheduled_for=monday,
        trigger="catch_up",
    )
    saturday = datetime(2026, 8, 22, 2, 0, tzinfo=timezone.utc)
    updated = service.complete_firing(firing.id, "succeeded", now=saturday)
    assert updated.next_run_at == "2026-08-24T10:00:00+00:00"


def test_skipped_firing_does_not_pause_job(isolated_db):
    service = _service()
    job = service.confirm(
        _weekly_job(service, name="跳过不停", failure_policy="pause_job").id,
        now=datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc),
    )
    firing = service.create_firing(
        job_id=job.id,
        scheduled_for=job.next_run_at or "",
        trigger="schedule",
    )
    updated = service.complete_firing(firing.id, "skipped")
    assert updated.status == "active"
    assert updated.next_run_at == "2026-08-28T10:00:00+00:00"


def test_list_jobs_skips_corrupt_row(isolated_db):
    service = _service()
    valid = _weekly_job(service, name="正常任务")
    now = "2026-08-21T00:00:00+00:00"
    with database._lock, database._connect() as conn:
        conn.execute(
            """
            INSERT INTO scheduled_jobs (
                id, workspace_id, project_id, name, status, agent_id,
                schedule_kind, schedule_expr, timezone, frozen_prompt,
                missed_policy, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "job-corrupt", DEFAULT_WORKSPACE_ID, DEFAULT_PROJECT_ID,
                "损坏任务", "pending_confirmation", "agent-1",
                "weekly", '{"weekdays":["fri"],"at":"18:00"}', "Asia/Shanghai",
                "prompt", "not-a-policy", now, now,
            ),
        )
        conn.commit()
    listed = service.list_jobs(project_id=DEFAULT_PROJECT_ID)
    assert [item.id for item in listed] == [valid.id]


def test_pause_for_resource_change_pauses_matching_active_jobs(isolated_db):
    service = _service()
    _ensure_skill("skill-report")
    now = datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc)
    job = service.confirm(
        _weekly_job(service, skill_ids=["skill-report"]).id,
        now=now,
    )
    other = service.confirm(
        _weekly_job(service, name="另一份", agent_id="agent-2").id,
        now=now,
    )
    paused = service.pause_for_resource_change(
        "skill_unbound",
        project_id=DEFAULT_PROJECT_ID,
        skill_id="skill-report",
    )
    assert [item.id for item in paused] == [job.id]
    assert service.get_job(job.id).status == "paused"
    assert service.get_job(job.id).pause_reason == "skill_unbound"
    assert service.get_job(other.id).status == "active"

    by_agent = service.pause_for_resource_change("agent_missing", agent_id="agent-2")
    assert [item.id for item in by_agent] == [other.id]
    notes = list_user_notifications(project_id=DEFAULT_PROJECT_ID)
    paused_notes = [item for item in notes if item["kind"] == "job_paused"]
    assert {item["job_id"] for item in paused_notes} == {job.id, other.id}


def test_user_pause_does_not_write_notification(isolated_db):
    service = _service()
    job = service.confirm(
        _weekly_job(service, name="用户暂停").id,
        now=datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc),
    )
    paused = service.pause(job.id)
    assert paused.status == "paused"
    assert list_user_notifications(project_id=DEFAULT_PROJECT_ID) == []


def test_cancelled_firing_is_not_a_failure_notification(isolated_db):
    service = _service()
    job = service.confirm(
        _weekly_job(service, name="取消不算失败", failure_policy="pause_job").id,
        now=datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc),
    )
    firing = service.create_firing(
        job_id=job.id,
        scheduled_for=to_utc_iso(datetime.now(timezone.utc)),
        trigger="manual",
    )
    updated = service.complete_firing(firing.id, "cancelled")
    assert updated.status == "active"
    assert updated.consecutive_failures == 0
    assert updated.pause_reason == ""
    assert list_user_notifications(project_id=DEFAULT_PROJECT_ID) == []


def test_firing_notification_title_allows_braces_in_job_name(isolated_db):
    service = _service()
    job = service.confirm(
        _weekly_job(service, name="周报 {draft").id,
        now=datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc),
    )
    firing = service.create_firing(
        job_id=job.id,
        scheduled_for=job.next_run_at or "",
        trigger="schedule",
    )
    service.complete_firing(firing.id, "succeeded")
    notes = list_user_notifications(project_id=DEFAULT_PROJECT_ID)
    completed = [item for item in notes if item["kind"] == "job_completed"]
    assert len(completed) == 1
    assert completed[0]["title"] == "周报 {draft 已完成"


def test_sql_tool_scope_default_is_all_false(isolated_db):
    ensure_db()
    now = "2026-08-21T00:00:00+00:00"
    with database._lock, database._connect() as conn:
        conn.execute(
            """
            INSERT INTO scheduled_jobs (
                id, workspace_id, project_id, name, status, agent_id,
                schedule_kind, schedule_expr, timezone, frozen_prompt,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "job-sql-default", DEFAULT_WORKSPACE_ID, DEFAULT_PROJECT_ID,
                "SQL默认", "pending_confirmation", "agent-1",
                "weekly", '{"weekdays":["fri"],"at":"18:00"}', "Asia/Shanghai",
                "prompt", now, now,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT tool_scope FROM scheduled_jobs WHERE id = ?",
            ("job-sql-default",),
        ).fetchone()
    assert json.loads(row["tool_scope"]) == DEFAULT_JOB_TOOL_SCOPE
    assert DEFAULT_JOB_TOOL_SCOPE_JSON in BASE_SCHEMA_SQL
    assert json.loads(DEFAULT_JOB_TOOL_SCOPE_JSON) == DEFAULT_JOB_TOOL_SCOPE


def test_schema_tables_and_unique_indexes_exist(isolated_db):
    ensure_db()
    with database._lock, database._connect() as conn:
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert {
            "scheduled_jobs",
            "scheduled_job_runs",
            "project_job_recipients",
            "user_notifications",
        }.issubset(tables)
        session_cols = {
            row["name"] for row in conn.execute("PRAGMA table_info(chat_sessions)").fetchall()
        }
        run_cols = {
            row["name"] for row in conn.execute("PRAGMA table_info(chat_runs)").fetchall()
        }
        assert "kind" in session_cols
        assert {"trigger_source", "scheduled_job_id", "scheduled_firing_id"} <= run_cols
        indexes = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        assert "idx_scheduled_jobs_due" in indexes


def test_chat_session_kind_survives_save_chat_store(isolated_db):
    ensure_db()
    save_chat_store({
        "sessions": [{
            "id": "sess-job",
            "title": "定时执行",
            "agent_id": "agent-1",
            "agent_name": "助手",
            "mode": "agent",
            "workspace_id": DEFAULT_WORKSPACE_ID,
            "project_id": DEFAULT_PROJECT_ID,
            "kind": "scheduled_job",
            "created_at": "2026-08-21T00:00:00+00:00",
            "updated_at": "2026-08-21T00:00:00+00:00",
            "message_count": 0,
        }],
        "messages": {},
    })
    loaded = load_chat_store()
    assert loaded["sessions"][0]["kind"] == "scheduled_job"
    store = ChatStore.model_validate(loaded)
    assert store.sessions[0].kind == "scheduled_job"
    save_chat_store(store.model_dump(mode="json", by_alias=True))
    reloaded = load_chat_store()
    assert reloaded["sessions"][0]["kind"] == "scheduled_job"


def test_chat_session_kind_defaults_to_chat(isolated_db):
    ensure_db()
    session = ChatSession(
        id="sess-chat",
        title="普通对话",
        agent_id="agent-1",
        agent_name="助手",
        created_at="2026-08-21T00:00:00+00:00",
        updated_at="2026-08-21T00:00:00+00:00",
    )
    assert session.kind == "chat"
    save_chat_store({"sessions": [session.model_dump()], "messages": {}})
    loaded = load_chat_store()
    assert loaded["sessions"][0]["kind"] == "chat"


def test_chat_run_trigger_source_defaults_to_user(isolated_db):
    ensure_db()
    upsert_chat_run({
        "id": "run-user",
        "sessionId": "sess-1",
        "agentId": "agent-1",
        "agentName": "助手",
        "task": "hello",
        "mode": "agent",
        "status": "completed",
    })
    record = get_chat_run("run-user")
    assert record is not None
    assert record["triggerSource"] == "user"
    assert record["scheduledJobId"] == ""
    assert record["scheduledFiringId"] == ""


def test_chat_run_records_scheduled_columns(isolated_db):
    ensure_db()
    upsert_chat_run({
        "id": "run-job",
        "sessionId": "sess-job",
        "agentId": "agent-1",
        "agentName": "助手",
        "task": "周报",
        "mode": "agent",
        "status": "running",
        "triggerSource": "scheduled_job",
        "scheduledJobId": "job-1",
        "scheduledFiringId": "firing-1",
    })
    record = get_chat_run("run-job")
    assert record is not None
    assert record["triggerSource"] == "scheduled_job"
    assert record["scheduledJobId"] == "job-1"
    assert record["scheduledFiringId"] == "firing-1"
    upsert_chat_run({**record, "status": "completed"})
    updated = get_chat_run("run-job")
    assert updated is not None
    assert updated["triggerSource"] == "scheduled_job"
    assert updated["scheduledJobId"] == "job-1"
    assert updated["status"] == "completed"
    upsert_chat_run({"id": "run-job", "status": "failed"})
    partial = get_chat_run("run-job")
    assert partial is not None
    assert partial["status"] == "failed"
    assert partial["triggerSource"] == "scheduled_job"
    assert partial["scheduledJobId"] == "job-1"
    assert partial["scheduledFiringId"] == "firing-1"
    assert partial["sessionId"] == "sess-job"
