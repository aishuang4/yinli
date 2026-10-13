"""进程内 JobScheduler ticker：只 INSERT queued 并 create_task，不 await 信号量或 ReAct。"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

from src.scheduler.firing import ensure_firing_terminal, run_firing
from src.scheduler.models import TERMINAL_FIRING_STATUSES, ScheduledJob
from src.scheduler.schedule import parse_stored_datetime, to_utc_iso
from src.scheduler.service import ScheduledJobService
from src.scheduler.settings import ensure_desktop_scheduled_jobs_defaults, is_scheduled_jobs_enabled
from src.runtime.scheduled_chat import reconcile_interrupted_chat_runs

logger = logging.getLogger(__name__)

MAX_SCHEDULED_FIRINGS = 2
STARTING_STALE_SECONDS = 60
TICK_INTERVAL_SECONDS = 15

_scheduler: JobScheduler | None = None


class JobScheduler:
    """15s 扫描到期 Job；queued 入队后把 Task 放入 _inflight。"""

    def __init__(
        self,
        *,
        service: ScheduledJobService | None = None,
        interval_seconds: float = TICK_INTERVAL_SECONDS,
        stale_seconds: float = STARTING_STALE_SECONDS,
        max_inflight: int = MAX_SCHEDULED_FIRINGS,
    ) -> None:
        self._service = service or ScheduledJobService()
        self._interval_seconds = max(0.05, float(interval_seconds))
        self._stale_seconds = max(0.0, float(stale_seconds))
        self._sem = asyncio.Semaphore(max(1, int(max_inflight)))
        self._inflight: set[asyncio.Task[Any]] = set()
        self._firing_tasks: dict[str, asyncio.Task[Any]] = {}
        self._ticker_task: asyncio.Task[Any] | None = None
        self._stopped = False

    @property
    def service(self) -> ScheduledJobService:
        return self._service

    def start(self) -> None:
        """启动 ticker 循环。flag 未启用时由调用方不要调用。"""

        if self._ticker_task is not None and not self._ticker_task.done():
            return
        self._stopped = False
        self._ticker_task = asyncio.create_task(
            self._loop(), name="job-scheduler-ticker",
        )

    def set_running(self, enabled: bool) -> None:
        """设置页开关：打开则扫描到期任务，关闭只停 ticker，不杀在途 firing。"""

        if enabled:
            self.start()
            return
        self._stopped = True
        ticker = self._ticker_task
        if ticker is not None and not ticker.done():
            ticker.cancel()
        self._ticker_task = None

    async def stop(self) -> None:
        """取消 ticker 与 in-flight Task，须在 close_db 之前调用。"""

        self._stopped = True
        ticker = self._ticker_task
        if ticker is not None and not ticker.done():
            ticker.cancel()
            await asyncio.gather(ticker, return_exceptions=True)
        self._ticker_task = None
        pending = list(self._inflight)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def tick(self, *, now: datetime | None = None) -> dict[str, int]:
        """扫描到期 Job：INSERT queued + create_task。绝不 await 信号量或 ReAct。"""

        stamped = now or datetime.now(timezone.utc)
        started = time.monotonic()
        stats = {
            "due_count": 0,
            "claimed": 0,
            "skipped_in_flight": 0,
            "missed": 0,
            "stale_reclaimed": 0,
            "clock_repaired": 0,
            "paused_daily_cap": 0,
        }
        stats["stale_reclaimed"] = self.reclaim_orphan_starting(now=stamped)
        due_jobs = self._service.list_due_jobs(now=stamped)
        stats["due_count"] = len(due_jobs)
        for job in due_jobs:
            self._dispatch_due_job(job, now=stamped, stats=stats)
        scan_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            "JobScheduler tick scan_ms=%s due_count=%s claimed=%s skipped_in_flight=%s missed=%s",
            scan_ms,
            stats["due_count"],
            stats["claimed"],
            stats["skipped_in_flight"],
            stats["missed"],
        )
        return stats

    def reclaim_orphan_starting(self, *, now: datetime | None = None) -> int:
        """只回收 starting + 空 chat_run_id + 无 inflight Task 且超过 stale 秒。"""

        stamped = now or datetime.now(timezone.utc)
        reclaimed = 0
        for firing in self._service.list_starting_without_chat_run():
            if firing.id in self._firing_tasks:
                continue
            age = _age_seconds(firing.started_at or firing.updated_at, stamped)
            if age < self._stale_seconds:
                continue
            ensure_firing_terminal(
                self._service,
                firing.id,
                status="failed",
                error_code="stale_starting",
                error_message="starting 超时且进程内无对应 Task",
            )
            reclaimed += 1
        return reclaimed

    def _dispatch_due_job(
        self,
        job: ScheduledJob,
        *,
        now: datetime,
        stats: dict[str, int],
    ) -> None:
        if self._service.has_non_terminal_firing(job.id):
            stats["skipped_in_flight"] += 1
            return
        if self._service.count_firings_today(job, now=now) >= job.max_firings_per_day:
            if job.status == "active":
                self._service.pause(job.id, reason="daily_cap")
                stats["paused_daily_cap"] += 1
            return
        scheduled_for = job.next_run_at or to_utc_iso(now)
        occupant = self._service.occupying_due_slot(job.id, scheduled_for)
        if occupant is not None:
            if occupant.status in TERMINAL_FIRING_STATUSES:
                repaired = self._service.repair_clock_if_slot_occupied(
                    job, scheduled_for, now=now,
                )
                if repaired is not None:
                    stats["clock_repaired"] += 1
            else:
                stats["skipped_in_flight"] += 1
            return
        action, trigger = self._service.decide_due_action(job, now=now)
        if action in {"missed", "skip_silent"}:
            firing = self._service.claim_queued(
                job_id=job.id,
                scheduled_for=scheduled_for,
                trigger=trigger,
            )
            if firing is None:
                repaired = self._service.repair_clock_if_slot_occupied(
                    job, scheduled_for, now=now,
                )
                if repaired is not None:
                    stats["clock_repaired"] += 1
                return
            ensure_firing_terminal(
                self._service,
                firing.id,
                status="missed",
                error_code="missed",
                error_message="错过计划时间",
            )
            stats["missed"] += 1
            return
        firing = self._service.claim_queued(
            job_id=job.id,
            scheduled_for=scheduled_for,
            trigger=trigger,
        )
        if firing is None:
            repaired = self._service.repair_clock_if_slot_occupied(
                job, scheduled_for, now=now,
            )
            if repaired is not None:
                stats["clock_repaired"] += 1
            return
        task = asyncio.create_task(
            run_firing(
                job.id,
                firing.id,
                service=self._service,
                semaphore=self._sem,
            ),
            name=f"job-{firing.id}",
        )
        self._track(task, firing.id)
        stats["claimed"] += 1

    def _track(self, task: asyncio.Task[Any], firing_id: str) -> None:
        self._inflight.add(task)
        self._firing_tasks[firing_id] = task

        def _done(done: asyncio.Task[Any]) -> None:
            self._inflight.discard(done)
            self._firing_tasks.pop(firing_id, None)

        task.add_done_callback(_done)

    async def _loop(self) -> None:
        while not self._stopped:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("JobScheduler tick failed")
            try:
                await asyncio.sleep(self._interval_seconds)
            except asyncio.CancelledError:
                raise


def get_job_scheduler() -> JobScheduler:
    """进程内单例，供 lifespan start/stop。"""

    global _scheduler
    if _scheduler is None:
        _scheduler = JobScheduler()
    return _scheduler


def reset_job_scheduler() -> None:
    """测试用：丢掉单例。"""

    global _scheduler
    _scheduler = None


def bootstrap_scheduler(*, now: datetime | None = None) -> dict[str, int]:
    """启动顺序：桌面缺省开关 → Chat 孤儿 → job_runs →（flag 开启时）start ticker。"""

    seeded = ensure_desktop_scheduled_jobs_defaults()
    try:
        from src.catalog.sync_jobs import persist_job_definition, sync_job_definitions
        from src.storage.database import list_scheduled_jobs
        from src.scheduler.service import ScheduledJobService

        service = ScheduledJobService()
        for item in list_scheduled_jobs():
            try:
                persist_job_definition(service.get_job(str(item["id"])))
            except Exception:
                logger.exception("同步 Job 定义失败 job=%s", item.get("id"))
                continue
        sync_job_definitions()
    except Exception:
        logger.exception("同步 Job 定义文件失败")
    chat_n = reconcile_interrupted_chat_runs()
    job_n = get_job_scheduler().service.reconcile_interrupted_job_runs(now=now)
    started = 0
    if is_scheduled_jobs_enabled():
        get_job_scheduler().start()
        started = 1
    if chat_n or job_n or started or seeded:
        logger.info(
            "scheduler bootstrap chat_runs=%s job_runs=%s ticker_started=%s defaults_seeded=%s",
            chat_n, job_n, started, 1 if seeded else 0,
        )
    return {
        "chat_runs": chat_n,
        "job_runs": job_n,
        "ticker_started": started,
        "defaults_seeded": 1 if seeded else 0,
    }


def apply_scheduler_enabled(enabled: bool) -> None:
    """设置页切换主开关时启动或停掉 ticker。"""

    get_job_scheduler().set_running(enabled)


async def shutdown_scheduler() -> None:
    """取消 ticker 与 inflight，须先于 close_db。"""

    global _scheduler
    if _scheduler is None:
        return
    await _scheduler.stop()


def _age_seconds(raw: str | None, now: datetime) -> float:
    if not raw:
        return 0.0
    try:
        started = parse_stored_datetime(raw)
    except ValueError:
        return 0.0
    return (now - started).total_seconds()
