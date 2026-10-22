"""JobScheduler ticker、错过策略、stale starting 与 Chat 孤儿 reconcile。"""
from __future__ import annotations


import asyncio
import inspect
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.scheduler.models import ScheduledJobConflictError
from src.scheduler.schedule import grace_seconds_for_schedule, to_utc_iso
from src.scheduler.service import ON_TIME_SLACK_SECONDS, ScheduledJobService
from src.scheduler.settings import (
    ensure_desktop_scheduled_jobs_defaults,
    is_capability_gate_enabled,
    is_scheduled_jobs_enabled,
    set_scheduled_jobs_enabled,
    set_scheduled_jobs_settings,
)
from src.scheduler.ticker import (
    MAX_SCHEDULED_FIRINGS,
    JobScheduler,
    bootstrap_scheduler,
    get_job_scheduler,
    reset_job_scheduler,
    shutdown_scheduler,
)
from src.server.routes.chat import background_runs as background_runs_module
from src.server.routes.chat.background_runs import (
    PROCESS_RESTART_ERROR,
    ChatBackgroundRun,
    has_live_chat_background_task,
    reconcile_interrupted_chat_runs,
)
from src.storage.engine import database
from src.storage.engine.database import (
    DEFAULT_PROJECT_ID,
    DEFAULT_WORKSPACE_ID,
    ensure_db,
)
from src.storage.records.chat_run_records import (
    get_chat_run,
    upsert_chat_run,
)
from src.storage.records.chat_session_records import list_chat_messages
from src.storage.records.scheduled_job_records import (
    list_user_notifications,
    update_scheduled_job,
    update_scheduled_job_run,
)

BACKEND_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _enable_jobs_for_direct_ticks(request, local_tenant):
    """tick 现在会跳过关闭调度的租户。直接调 tick 的用例先打开开关。"""

    if request.node.name in {
        "test_feature_flag_false_does_not_start_ticker",
        "test_desktop_first_run_seeds_scheduler_enabled",
        "test_lifespan_reconciles_chat_then_jobs_then_starts_and_stops_before_close_db",
    }:
        yield
        return
    set_scheduled_jobs_enabled(True)
    yield


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    """每个用例独立 SQLite，并收口 ticker / Chat Run。"""

    monkeypatch.setattr("src.config.runtime_paths.USER_DATA_ROOT", tmp_path / "user-data")
    yield
    for run in list(background_runs_module._runs.values()):
        if run.async_task is not None and not run.async_task.done():
            run.async_task.cancel()
    background_runs_module._runs.clear()
    reset_job_scheduler()
    database.forget_db(database.db_path())


def _service() -> ScheduledJobService:
    ensure_db()
    return ScheduledJobService()


def _weekly_job(service: ScheduledJobService, **overrides):
    payload = {
        
        "project_slug": DEFAULT_PROJECT_ID,
        "name": "周五周报",
        "agent_id": "default",
        "schedule_kind": "weekly",
        "schedule_expr": {"weekdays": ["fri"], "at": "18:00"},
        "timezone": "Asia/Shanghai",
        "frozen_prompt": "生成本周周报，写入 outputs/weekly-report.md",
    }
    payload.update(overrides)
    return service.create_job(**payload)


def _confirm_friday(service: ScheduledJobService, **overrides):
    job = _weekly_job(service, **overrides)
    return service.confirm(job.id, now=datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc))


def _patch_hanging_start(monkeypatch, hang: asyncio.Event):
    async def fake_start(req):
        await hang.wait()
        return f"run-{req.firing_id}"

    async def fake_await(chat_run_id, *, timeout_seconds):
        _ = chat_run_id, timeout_seconds
        return "completed"

    monkeypatch.setattr("src.scheduler.firing.start_scheduled_chat_run", fake_start)
    monkeypatch.setattr("src.scheduler.firing.await_scheduled_chat_run", fake_await)
    monkeypatch.setattr("src.scheduler.firing._scan_outputs", lambda job, firing: {"files": []})


def _patch_instant_start(monkeypatch):
    async def fake_start(req):
        return f"run-{req.firing_id}"

    async def fake_await(chat_run_id, *, timeout_seconds):
        _ = chat_run_id, timeout_seconds
        return "completed"

    monkeypatch.setattr("src.scheduler.firing.start_scheduled_chat_run", fake_start)
    monkeypatch.setattr("src.scheduler.firing.await_scheduled_chat_run", fake_await)
    monkeypatch.setattr("src.scheduler.firing._scan_outputs", lambda job, firing: {"files": []})


def test_weekly_grace_is_six_hours():
    assert grace_seconds_for_schedule("weekly", {"weekdays": ["fri"], "at": "18:00"}) == 6 * 3600
    assert ON_TIME_SLACK_SECONDS == 60


def test_lifespan_reconciles_chat_then_jobs_then_starts_and_stops_before_close_db():
    source = (BACKEND_ROOT / "src/server/main.py").read_text(encoding="utf-8")
    assert "bootstrap_tenant_scheduler" in source
    assert "start_if_needed" in source
    assert "shutdown_scheduler" in source
    assert source.index("bootstrap_tenant_scheduler") < source.index("yield")
    assert source.index("start_if_needed") < source.index("yield")
    assert source.index("await shutdown_scheduler()") < source.index("await close_db()")
    ticker_src = (BACKEND_ROOT / "src/scheduler/ticker.py").read_text(encoding="utf-8")
    assert ticker_src.index("reconcile_interrupted_chat_runs") < ticker_src.index(
        "reconcile_interrupted_job_runs"
    )
    tick_fn = inspect.getsource(JobScheduler.tick)
    assert "async with" not in tick_fn
    assert "await run_firing" not in tick_fn
    assert "await self._sem" not in tick_fn


def test_feature_flag_false_does_not_start_ticker(isolated_db):
    ensure_db()
    assert is_scheduled_jobs_enabled() is False

    async def run_case():
        reset_job_scheduler()
        stats = bootstrap_scheduler()
        assert stats["ticker_started"] == 0
        assert get_job_scheduler()._ticker_task is None
        set_scheduled_jobs_enabled(True)
        stats = bootstrap_scheduler()
        assert stats["ticker_started"] == 1
        assert get_job_scheduler()._ticker_task is not None
        await shutdown_scheduler()
        assert get_job_scheduler()._ticker_task is None

    asyncio.run(run_case())


def test_desktop_first_run_seeds_scheduler_enabled(isolated_db):
    ensure_db()
    assert is_scheduled_jobs_enabled() is False
    seeded = ensure_desktop_scheduled_jobs_defaults(desktop=True)
    assert seeded is not None
    assert is_scheduled_jobs_enabled() is True
    assert is_capability_gate_enabled() is True
    set_scheduled_jobs_settings(enabled=False, capability_gate=False)
    assert ensure_desktop_scheduled_jobs_defaults(desktop=True) is None
    assert is_scheduled_jobs_enabled() is False
    assert ensure_desktop_scheduled_jobs_defaults(desktop=False) is None


def test_claim_queued_inserts_unique_and_skips_duplicate(isolated_db):
    service = _service()
    job = _confirm_friday(service)
    scheduled_for = job.next_run_at or ""
    first = service.claim_queued(
        job_id=job.id, scheduled_for=scheduled_for, trigger="schedule",
    )
    assert first is not None
    assert first.status == "queued"
    again = service.claim_queued(
        job_id=job.id, scheduled_for=scheduled_for, trigger="schedule",
    )
    assert again is None
    with pytest.raises(ScheduledJobConflictError):
        service.create_firing(
            job_id=job.id, scheduled_for=scheduled_for, trigger="schedule",
        )


def test_tick_claims_queued_without_waiting_semaphore_or_react(isolated_db, monkeypatch):
    service = _service()
    jobs = [
        _confirm_friday(service, name=f"任务{i}")
        for i in range(3)
    ]
    hang = asyncio.Event()
    _patch_hanging_start(monkeypatch, hang)
    scheduler = JobScheduler(service=service, stale_seconds=60)
    now = datetime(2026, 8, 21, 10, 0, 1, tzinfo=timezone.utc)

    async def run_case():
        started = time.monotonic()
        stats = await scheduler.tick(now=now)
        elapsed = time.monotonic() - started
        assert elapsed < 0.3
        assert stats["claimed"] == 3
        statuses: list[str] = []
        for _ in range(50):
            statuses = [
                item.status
                for job in jobs
                for item in service.list_firings(job.id)
            ]
            if (
                statuses.count("starting") == MAX_SCHEDULED_FIRINGS
                and statuses.count("queued") == 1
            ):
                break
            await asyncio.sleep(0.02)
        assert statuses.count("starting") == MAX_SCHEDULED_FIRINGS
        assert statuses.count("queued") == 1
        assert all(item.chat_run_id == "" for job in jobs for item in service.list_firings(job.id))
        hang.set()
        if scheduler._inflight:
            await asyncio.gather(*list(scheduler._inflight), return_exceptions=True)
        await scheduler.stop()

    asyncio.run(run_case())


def test_queued_is_not_stale_even_when_old(isolated_db):
    service = _service()
    job = _confirm_friday(service)
    firing = service.claim_queued(
        job_id=job.id,
        scheduled_for=job.next_run_at or "",
        trigger="schedule",
    )
    assert firing is not None
    scheduler = JobScheduler(service=service, stale_seconds=0)
    now = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
    reclaimed = scheduler.reclaim_orphan_starting(now=now)
    assert reclaimed == 0
    assert service.get_firing(firing.id).status == "queued"


def test_stale_starting_without_task_is_failed_and_clock_advances(isolated_db):
    service = _service()
    job = _confirm_friday(service)
    scheduled_for = job.next_run_at or ""
    firing = service.claim_queued(
        job_id=job.id, scheduled_for=scheduled_for, trigger="schedule",
    )
    assert firing is not None
    started_at = to_utc_iso(datetime(2026, 8, 21, 10, 0, tzinfo=timezone.utc))
    update_scheduled_job_run(firing.id, {
        "status": "starting",
        "started_at": started_at,
        "chat_run_id": "",
    })
    scheduler = JobScheduler(service=service, stale_seconds=60)
    now = datetime(2026, 8, 21, 10, 2, tzinfo=timezone.utc)
    reclaimed = scheduler.reclaim_orphan_starting(now=now)
    assert reclaimed == 1
    updated = service.get_firing(firing.id)
    assert updated.status == "failed"
    assert updated.error_code == "stale_starting"
    clock = service.get_job(job.id)
    assert clock.next_run_at == "2026-08-28T10:00:00+00:00"


def test_starting_with_inflight_task_is_not_stale(isolated_db):
    service = _service()
    job = _confirm_friday(service)
    firing = service.claim_queued(
        job_id=job.id,
        scheduled_for=job.next_run_at or "",
        trigger="schedule",
    )
    assert firing is not None
    update_scheduled_job_run(firing.id, {
        "status": "starting",
        "started_at": to_utc_iso(datetime(2026, 8, 21, 10, 0, tzinfo=timezone.utc)),
        "chat_run_id": "",
    })
    scheduler = JobScheduler(service=service, stale_seconds=0)

    async def run_case():
        blocker = asyncio.Event()

        async def dummy():
            await blocker.wait()

        task = asyncio.create_task(dummy())
        scheduler._inflight.add(task)
        scheduler._firing_tasks[firing.id] = task
        reclaimed = scheduler.reclaim_orphan_starting(
            now=datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc),
        )
        assert reclaimed == 0
        assert service.get_firing(firing.id).status == "starting"
        blocker.set()
        await task

    asyncio.run(run_case())


def test_missed_policy_mark_missed_finalizes_clock(isolated_db):
    service = _service()
    job = _confirm_friday(service, name="标记错过", missed_policy="mark_missed")
    scheduler = JobScheduler(service=service)
    now = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)

    async def run_case():
        stats = await scheduler.tick(now=now)
        assert stats["missed"] == 1
        assert stats["claimed"] == 0
        firing = service.list_firings(job.id)[0]
        assert firing.status == "missed"
        assert firing.error_code == "missed"
        updated = service.get_job(job.id)
        assert updated.next_run_at == "2026-08-28T10:00:00+00:00"
        assert updated.status == "active"

    asyncio.run(run_case())


def test_catch_up_once_within_grace_uses_catch_up_trigger(isolated_db, monkeypatch):
    service = _service()
    job = _confirm_friday(service, name="补跑一次")
    _patch_instant_start(monkeypatch)
    scheduler = JobScheduler(service=service)
    now = datetime(2026, 8, 21, 14, 0, tzinfo=timezone.utc)

    async def run_case():
        stats = await scheduler.tick(now=now)
        assert stats["claimed"] == 1
        if scheduler._inflight:
            await asyncio.gather(*list(scheduler._inflight), return_exceptions=True)
        firing = service.list_firings(job.id)[0]
        assert firing.trigger == "catch_up"
        assert firing.status == "succeeded"
        updated = service.get_job(job.id)
        assert updated.next_run_at == "2026-08-28T10:00:00+00:00"
        await scheduler.stop()

    asyncio.run(run_case())


def test_catch_up_beyond_grace_is_missed(isolated_db):
    service = _service()
    job = _confirm_friday(service, name="超过宽限")
    scheduler = JobScheduler(service=service)
    now = datetime(2026, 8, 21, 17, 0, tzinfo=timezone.utc)

    async def run_case():
        stats = await scheduler.tick(now=now)
        assert stats["missed"] == 1
        firing = service.list_firings(job.id)[0]
        assert firing.status == "missed"
        assert firing.trigger == "schedule"
        assert service.get_job(job.id).next_run_at == "2026-08-28T10:00:00+00:00"

    asyncio.run(run_case())


def test_succeeded_firing_writes_job_completed_notification(isolated_db, monkeypatch):
    service = _service()
    job = _confirm_friday(service, name="成功通知")
    _patch_instant_start(monkeypatch)
    scheduler = JobScheduler(service=service)
    now = datetime(2026, 8, 21, 10, 0, 0, tzinfo=timezone.utc)

    async def run_case():
        await scheduler.tick(now=now)
        if scheduler._inflight:
            await asyncio.gather(*list(scheduler._inflight), return_exceptions=True)
        firing = service.list_firings(job.id)[0]
        assert firing.status == "succeeded"
        notes = list_user_notifications(project_slug=DEFAULT_PROJECT_ID)
        completed = [item for item in notes if item["kind"] == "job_completed"]
        assert len(completed) == 1
        assert completed[0]["job_id"] == job.id
        assert completed[0]["firing_id"] == firing.id
        service.complete_firing(firing.id, "succeeded")
        again = list_user_notifications(project_slug=DEFAULT_PROJECT_ID)
        assert len([item for item in again if item["kind"] == "job_completed"]) == 1
        await scheduler.stop()

    asyncio.run(run_case())


def test_missed_firing_writes_job_missed_notification(isolated_db):
    service = _service()
    job = _confirm_friday(service, name="错过通知", missed_policy="mark_missed")
    scheduler = JobScheduler(service=service)
    now = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)

    async def run_case():
        stats = await scheduler.tick(now=now)
        assert stats["missed"] == 1
        firing = service.list_firings(job.id)[0]
        assert firing.status == "missed"
        notes = list_user_notifications(project_slug=DEFAULT_PROJECT_ID)
        missed = [item for item in notes if item["kind"] == "job_missed"]
        assert len(missed) == 1
        assert missed[0]["job_id"] == job.id
        assert missed[0]["firing_id"] == firing.id
        assert all(item["kind"] != "job_completed" for item in notes)

    asyncio.run(run_case())


def test_skip_silent_missed_does_not_write_notification(isolated_db):
    service = _service()
    job = _confirm_friday(service, name="静默错过", missed_policy="skip_silent")
    scheduler = JobScheduler(service=service)
    now = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)

    async def run_case():
        stats = await scheduler.tick(now=now)
        assert stats["missed"] == 1
        firing = service.list_firings(job.id)[0]
        assert firing.status == "missed"
        notes = list_user_notifications(project_slug=DEFAULT_PROJECT_ID)
        assert notes == []

    asyncio.run(run_case())


def test_on_time_tick_uses_schedule_trigger(isolated_db, monkeypatch):
    service = _service()
    job = _confirm_friday(service, name="准时")
    _patch_instant_start(monkeypatch)
    scheduler = JobScheduler(service=service)
    now = datetime(2026, 8, 21, 10, 0, 0, tzinfo=timezone.utc)

    async def run_case():
        await scheduler.tick(now=now)
        if scheduler._inflight:
            await asyncio.gather(*list(scheduler._inflight), return_exceptions=True)
        firing = service.list_firings(job.id)[0]
        assert firing.trigger == "schedule"
        assert firing.status == "succeeded"
        await scheduler.stop()

    asyncio.run(run_case())


def test_unique_conflict_repairs_clock_when_terminal_row_exists(isolated_db):
    service = _service()
    job = _confirm_friday(service, name="时钟损坏")
    scheduled_for = job.next_run_at or ""
    firing = service.create_firing(
        job_id=job.id, scheduled_for=scheduled_for, trigger="schedule",
    )
    service.complete_firing(firing.id, "succeeded")
    update_scheduled_job(job.id, {"next_run_at": scheduled_for, "status": "active"})
    broken = service.get_job(job.id)
    assert broken.next_run_at == scheduled_for
    scheduler = JobScheduler(service=service)
    now = datetime(2026, 8, 21, 10, 0, 1, tzinfo=timezone.utc)

    async def run_case():
        stats = await scheduler.tick(now=now)
        assert stats["clock_repaired"] == 1
        assert stats["claimed"] == 0
        assert service.get_job(job.id).next_run_at == "2026-08-28T10:00:00+00:00"

    asyncio.run(run_case())


def test_catch_up_does_not_refire_same_scheduled_for_after_stuck_clock(
    isolated_db, monkeypatch,
):
    """终态 schedule 行未推时钟时，catch_up_once 不得再 INSERT 同一到期点。"""

    service = _service()
    job = _confirm_friday(service, name="崩溃后补跑")
    scheduled_for = job.next_run_at or ""
    firing = service.create_firing(
        job_id=job.id, scheduled_for=scheduled_for, trigger="schedule",
    )
    service.complete_firing(firing.id, "succeeded")
    update_scheduled_job(job.id, {"next_run_at": scheduled_for, "status": "active"})
    starts: list[str] = []

    async def fake_start(req):
        starts.append(req.firing_id)
        return f"run-{req.firing_id}"

    monkeypatch.setattr("src.scheduler.firing.start_scheduled_chat_run", fake_start)
    scheduler = JobScheduler(service=service)
    now = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)

    async def run_case():
        stats = await scheduler.tick(now=now)
        if scheduler._inflight:
            await asyncio.gather(*list(scheduler._inflight), return_exceptions=True)
        assert stats["claimed"] == 0
        assert stats["clock_repaired"] == 1
        assert starts == []
        rows = service.list_firings(job.id)
        assert len(rows) == 1
        assert rows[0].trigger == "schedule"
        assert rows[0].status == "succeeded"
        assert service.get_job(job.id).next_run_at == "2026-08-28T10:00:00+00:00"
        await scheduler.stop()

    asyncio.run(run_case())


def test_reconcile_repairs_stuck_clock_on_terminal_firing(isolated_db):
    service = _service()
    job = _confirm_friday(service, name="启动补时钟")
    scheduled_for = job.next_run_at or ""
    firing = service.create_firing(
        job_id=job.id, scheduled_for=scheduled_for, trigger="schedule",
    )
    service.complete_firing(firing.id, "succeeded")
    update_scheduled_job(job.id, {"next_run_at": scheduled_for, "status": "active"})
    count = service.reconcile_interrupted_job_runs(
        now=datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc),
    )
    assert count == 1
    assert len(service.list_firings(job.id)) == 1
    assert service.get_firing(firing.id).status == "succeeded"
    assert service.get_job(job.id).next_run_at == "2026-08-28T10:00:00+00:00"


def test_daily_cap_pauses_job(isolated_db):
    service = _service()
    job = _confirm_friday(service, name="每日上限", max_firings_per_day=1)
    manual = service.create_firing(
        job_id=job.id,
        scheduled_for=to_utc_iso(datetime.now(timezone.utc)),
        trigger="manual",
    )
    service.complete_firing(manual.id, "succeeded")
    scheduler = JobScheduler(service=service)
    now = datetime.now(timezone.utc)

    async def run_case():
        stats = await scheduler.tick(now=now)
        assert stats["paused_daily_cap"] == 1
        assert stats["claimed"] == 0
        assert service.get_job(job.id).status == "paused"
        assert service.get_job(job.id).pause_reason == "daily_cap"

    asyncio.run(run_case())


def test_run_firing_finally_finalizes_on_launch_error(isolated_db, monkeypatch):
    service = _service()
    job = _confirm_friday(service, name="启动失败")

    async def boom(req):
        raise RuntimeError("bridge down")

    monkeypatch.setattr("src.scheduler.firing.start_scheduled_chat_run", boom)
    scheduler = JobScheduler(service=service)
    now = datetime(2026, 8, 21, 10, 0, 0, tzinfo=timezone.utc)

    async def run_case():
        await scheduler.tick(now=now)
        if scheduler._inflight:
            await asyncio.gather(*list(scheduler._inflight), return_exceptions=True)
        firing = service.list_firings(job.id)[0]
        assert firing.status == "failed"
        assert service.get_job(job.id).next_run_at == "2026-08-28T10:00:00+00:00"
        await scheduler.stop()

    asyncio.run(run_case())


def test_reconcile_interrupted_job_runs_fails_and_finalizes_clock(isolated_db):
    service = _service()
    job = _confirm_friday(service, name="进程重启 firing")
    firing = service.claim_queued(
        job_id=job.id,
        scheduled_for=job.next_run_at or "",
        trigger="schedule",
    )
    assert firing is not None
    count = service.reconcile_interrupted_job_runs(
        now=datetime(2026, 8, 21, 11, 0, tzinfo=timezone.utc),
    )
    assert count == 1
    updated = service.get_firing(firing.id)
    assert updated.status == "failed"
    assert updated.error_code == "process_restart"
    assert service.get_job(job.id).next_run_at == "2026-08-28T10:00:00+00:00"


def test_reconcile_interrupted_chat_runs_fails_orphans_and_skips_live_tasks(isolated_db):
    ensure_db()
    upsert_chat_run({
        "id": "run-orphan",
        "sessionId": "sess-orphan",
        "agentId": "agent-1",
        "agentName": "助手",
        "task": "hello",
        "mode": "agent",
        "status": "waiting_user",
    })
    upsert_chat_run({
        "id": "run-running-orphan",
        "sessionId": "sess-running",
        "status": "running",
        "task": "live",
        "mode": "agent",
    })
    upsert_chat_run({
        "id": "run-live",
        "sessionId": "sess-live",
        "status": "running",
        "task": "keep",
        "mode": "agent",
    })

    async def run_case():
        blocker = asyncio.Event()

        async def dummy():
            await blocker.wait()

        task = asyncio.create_task(dummy())
        live = ChatBackgroundRun(
            id="run-live",
            session_id="sess-live",
            status="running",
        )
        live.async_task = task
        background_runs_module._runs["run-live"] = live
        assert has_live_chat_background_task("run-live") is True
        count = reconcile_interrupted_chat_runs()
        assert count == 2
        assert get_chat_run("run-orphan")["status"] == "failed"
        assert get_chat_run("run-running-orphan")["status"] == "failed"
        assert get_chat_run("run-live")["status"] == "running"
        errors = [
            item.get("error")
            for item in list_chat_messages("sess-orphan")
        ]
        assert PROCESS_RESTART_ERROR in errors
        blocker.set()
        await task

    asyncio.run(run_case())


def test_bootstrap_reconciles_chat_before_jobs(isolated_db):
    service = _service()
    job = _confirm_friday(service, name="启动收口")
    firing = service.claim_queued(
        job_id=job.id,
        scheduled_for=job.next_run_at or "",
        trigger="schedule",
    )
    assert firing is not None
    upsert_chat_run({
        "id": "run-boot",
        "sessionId": "sess-boot",
        "status": "waiting_permission",
        "task": "hitl",
        "mode": "agent",
    })
    reset_job_scheduler()
    stats = bootstrap_scheduler(
        now=datetime(2026, 8, 21, 11, 0, tzinfo=timezone.utc),
    )
    assert stats["ticker_started"] == 0
    assert stats["chat_runs"] == 1
    assert stats["job_runs"] == 1
    assert get_chat_run("run-boot")["status"] == "failed"
    assert service.get_firing(firing.id).status == "failed"
    assert service.get_firing(firing.id).error_code == "process_restart"
    assert service.get_job(job.id).next_run_at == "2026-08-28T10:00:00+00:00"


def test_scheduler_stop_cancels_inflight(isolated_db, monkeypatch):
    service = _service()
    job = _confirm_friday(service, name="取消中")
    hang = asyncio.Event()
    _patch_hanging_start(monkeypatch, hang)
    scheduler = JobScheduler(service=service)
    now = datetime(2026, 8, 21, 10, 0, 0, tzinfo=timezone.utc)

    async def run_case():
        await scheduler.tick(now=now)
        await asyncio.sleep(0.05)
        assert scheduler._inflight
        await scheduler.stop()
        assert not scheduler._inflight
        firing = service.list_firings(job.id)[0]
        assert firing.status in {"cancelled", "failed"}
        assert service.get_job(job.id).next_run_at == "2026-08-28T10:00:00+00:00"

    asyncio.run(run_case())
