"""定时任务通知适配器。领域事件转交通知分发器，不直接写 SQLite。"""

from __future__ import annotations

import logging
from typing import Any

from src.notifications.dispatcher import dispatch_notification
from src.notifications.inbox import (
    NotificationNotFoundError,
    get_notification,
    list_notification_page,
    mark_all_read,
    mark_read,
    unread_count,
)
from src.notifications.models import NotificationMessage
from src.scheduler.models import ScheduledJob, ScheduledJobRun

logger = logging.getLogger(__name__)

JOB_COMPLETED = "job_completed"
JOB_FAILED = "job_failed"
JOB_MISSED = "job_missed"
JOB_PAUSED = "job_paused"

_FIRING_KIND = {
    "succeeded": JOB_COMPLETED,
    "failed": JOB_FAILED,
    "missed": JOB_MISSED,
}
_FIRING_TITLE_SUFFIX = {
    "succeeded": "已完成",
    "failed": "执行失败",
    "missed": "已错过计划时间",
}


def create_notification(
    *,
    kind: str,
    title: str,
    project_slug: str,
    job_id: str = "",
    firing_id: str = "",
    body: str = "",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """写入一条通知并分发给已启用通道。"""

    result = dispatch_notification(NotificationMessage(
        kind=kind,
        title=title,
        project_slug=project_slug,
        job_id=job_id,
        firing_id=firing_id,
        body=body,
        payload=dict(payload or {}),
        source="scheduled_job",
    ))
    if result.in_app_record is None:
        raise RuntimeError("应用内通知未落库")
    return result.in_app_record


def notify_firing_terminal(
    job: ScheduledJob,
    firing: ScheduledJobRun,
) -> dict[str, Any] | None:
    """firing 终态写入应用内通知；cancel / skip_silent 不通知。"""

    if firing.status == "missed" and job.missed_policy == "skip_silent":
        return None
    kind = _FIRING_KIND.get(firing.status)
    suffix = _FIRING_TITLE_SUFFIX.get(firing.status)
    if not kind or not suffix:
        return None
    body = firing.error_message or ""
    if firing.status == "succeeded" and not body:
        result_text = str((firing.output_manifest or {}).get("resultText") or "").strip()
        body = result_text[:800] if result_text else "定时任务已成功完成"
    elif firing.status == "missed" and not body:
        body = "错过计划时间"
    try:
        return create_notification(
            kind=kind,
            title=f"{job.name} {suffix}",
            project_slug=job.project_slug,
            job_id=job.id,
            firing_id=firing.id,
            body=body,
            payload={
                "status": firing.status,
                "error_code": firing.error_code,
                "scheduled_for": firing.scheduled_for,
                "trigger": firing.trigger,
            },
        )
    except Exception:
        logger.exception("写入 firing 通知失败 job=%s firing=%s", job.id, firing.id)
        return None


def notify_job_paused(
    job: ScheduledJob,
    *,
    firing: ScheduledJobRun | None = None,
) -> dict[str, Any] | None:
    """自动暂停写入应用内通知。"""

    reason = job.pause_reason or "paused"
    try:
        return create_notification(
            kind=JOB_PAUSED,
            title=f"{job.name} 已自动暂停",
            project_slug=job.project_slug,
            job_id=job.id,
            firing_id=firing.id if firing is not None else "",
            body=reason,
            payload={"pause_reason": reason},
        )
    except Exception:
        logger.exception("写入自动暂停通知失败 job=%s", job.id)
        return None


__all__ = [
    "JOB_COMPLETED",
    "JOB_FAILED",
    "JOB_MISSED",
    "JOB_PAUSED",
    "NotificationNotFoundError",
    "create_notification",
    "get_notification",
    "list_notification_page",
    "mark_all_read",
    "mark_read",
    "notify_firing_terminal",
    "notify_job_paused",
    "unread_count",
]
