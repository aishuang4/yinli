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
from src.scheduler.settings import ensure_scheduled_jobs_defaults, is_scheduled_jobs_enabled
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
        self._firing_tenants: dict[str, str] = {}
        self._ticker_task: asyncio.Task[Any] | None = None
        self._stopped = False

    @property
    def service(self) -> ScheduledJobService:
        return self._service

    def start(self) -> bool:
        """启动 ticker 循环。没有正在运行的事件循环时不启动。"""

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return False
        if self._ticker_task is not None and not self._ticker_task.done():
            return True
        self._stopped = False
        self._ticker_task = asyncio.create_task(
            self._loop(), name="job-scheduler-ticker",
        )
        return True

    def start_if_needed(self) -> bool:
        """desktop 沿用租户开关；hosted 的 ticker 总是启动，开关在 tick 内判断。"""

        from src.tenancy.context import is_hosted_mode

        if is_hosted_mode() or is_scheduled_jobs_enabled():
            return self.start()
        return False

    def set_running(self, enabled: bool) -> None:
        """设置页开关。hosted 不启停进程级 ticker，只由调用方写入当前租户设置。"""

        from src.tenancy.context import is_hosted_mode

        if is_hosted_mode():
            return
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
        """按租户扫描到期 Job。单个租户失败不挡住其他租户。"""

        from src.tenancy.context import use_tenant
        from src.tenancy.lifecycle import iter_tick_tenants

        stamped = now or datetime.now(timezone.utc)
        totals = {
            "due_count": 0,
            "claimed": 0,
            "skipped_in_flight": 0,
            "missed": 0,
            "stale_reclaimed": 0,
            "clock_repaired": 0,
            "paused_daily_cap": 0,
        }
        for ctx in iter_tick_tenants():
            with use_tenant(ctx):
                if not is_scheduled_jobs_enabled():
                    continue
                try:
                    stats = self._tick_current_tenant(now=stamped)
                except Exception:
                    logger.exception("tick 失败 tenant=%s", ctx.tenant_id)
                    continue
            for key, value in stats.items():
                totals[key] = totals.get(key, 0) + int(value)
        return totals

    def _tick_current_tenant(self, *, now: datetime) -> dict[str, int]:
        """扫描当前租户的到期 Job。绝不 await 信号量或 ReAct。"""

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
        stats["stale_reclaimed"] = self.reclaim_orphan_starting(now=now)
        due_jobs = self._service.list_due_jobs(now=now)
        stats["due_count"] = len(due_jobs)
        for job in due_jobs:
            self._dispatch_due_job(job, now=now, stats=stats)
        scan_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            "JobScheduler tick tenant scan_ms=%s due_count=%s claimed=%s skipped_in_flight=%s missed=%s",
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
        from src.tenancy.context import current_tenant

        self._inflight.add(task)
        self._firing_tasks[firing_id] = task
        self._firing_tenants[firing_id] = current_tenant().tenant_id

        def _done(done: asyncio.Task[Any]) -> None:
            self._inflight.discard(done)
            self._firing_tasks.pop(firing_id, None)
            self._firing_tenants.pop(firing_id, None)

        task.add_done_callback(_done)

    def cancel_tenant_firings(self, tenant_id: str) -> None:
        """取消属于这个租户、还在跑的 firing 任务。"""

        for firing_id, owner in list(self._firing_tenants.items()):
            if owner != tenant_id:
                continue
            task = self._firing_tasks.get(firing_id)
            if task is not None and not task.done():
                task.cancel()

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


def bootstrap_tenant_scheduler(*, now: datetime | None = None) -> dict[str, int]:
    """当前租户内：默认开关、Job 文件、中断的 Chat Run 和 firing。"""

    seeded = ensure_scheduled_jobs_defaults()
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
    if chat_n or job_n or seeded:
        logger.info(
            "scheduler tenant bootstrap chat_runs=%s job_runs=%s defaults_seeded=%s",
            chat_n, job_n, 1 if seeded else 0,
        )
    return {
        "chat_runs": chat_n,
        "job_runs": job_n,
        "defaults_seeded": 1 if seeded else 0,
    }


def bootstrap_scheduler(*, now: datetime | None = None) -> dict[str, int]:
    """对当前租户对账，再按模式决定是否启动进程级 ticker。"""

    stats = bootstrap_tenant_scheduler(now=now)
    started = 1 if get_job_scheduler().start_if_needed() else 0
    stats["ticker_started"] = started
    return stats


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
