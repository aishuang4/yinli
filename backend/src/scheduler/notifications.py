"""应用内通知读写。本模块不推送 Chat SSE，也不启动 ticker。"""

from __future__ import annotations

from typing import Any

from src.scheduler.models import ScheduledJob, ScheduledJobRun
from src.storage.database import (
    count_unread_user_notifications,
    get_user_notification,
    insert_user_notification,
    list_user_notifications,
    mark_all_user_notifications_read,
    mark_user_notification_read,
)

JOB_COMPLETED = "job_completed"
JOB_FAILED = "job_failed"
JOB_MISSED = "job_missed"
JOB_PAUSED = "job_paused"

_FIRING_KIND = {
    "succeeded": JOB_COMPLETED,
    "failed": JOB_FAILED,
    "cancelled": JOB_FAILED,
    "missed": JOB_MISSED,
}
_FIRING_TITLE = {
    "succeeded": "{name} 已完成",
    "failed": "{name} 执行失败",
    "cancelled": "{name} 已取消",
    "missed": "{name} 已错过计划时间",
}


class NotificationNotFoundError(Exception):
    """通知不存在。"""


def create_notification(
    *,
    kind: str,
    title: str,
    workspace_id: str,
    project_id: str = "",
    job_id: str = "",
    firing_id: str = "",
    body: str = "",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """写入一条通知，供控制面与测试插入。"""

    if not str(kind).strip():
        raise ValueError("kind 不能为空")
    if not str(title).strip():
        raise ValueError("title 不能为空")
    return insert_user_notification({
        "workspace_id": workspace_id,
        "project_id": project_id,
        "job_id": job_id,
        "firing_id": firing_id,
        "kind": kind,
        "title": title,
        "body": body,
        "payload": dict(payload or {}),
    })


def notify_firing_terminal(
    job: ScheduledJob,
    firing: ScheduledJobRun,
) -> dict[str, Any] | None:
    """firing 终态写入应用内通知；skip_silent 错过不通知。"""

    if firing.status == "missed" and job.missed_policy == "skip_silent":
        return None
    kind = _FIRING_KIND.get(firing.status)
    if not kind:
        return None
    body = firing.error_message or ""
    if firing.status == "succeeded" and not body:
        body = "定时任务已成功完成"
    elif firing.status == "missed" and not body:
        body = "错过计划时间"
    return create_notification(
        kind=kind,
        title=_FIRING_TITLE[firing.status].format(name=job.name),
        workspace_id=job.workspace_id,
        project_id=job.project_id,
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


def notify_job_paused(
    job: ScheduledJob,
    *,
    firing: ScheduledJobRun | None = None,
) -> dict[str, Any]:
    """自动暂停写入应用内通知。"""

    reason = job.pause_reason or "paused"
    return create_notification(
        kind=JOB_PAUSED,
        title=f"{job.name} 已自动暂停",
        workspace_id=job.workspace_id,
        project_id=job.project_id,
        job_id=job.id,
        firing_id=firing.id if firing is not None else "",
        body=reason,
        payload={"pause_reason": reason},
    )


def list_notification_page(
    *,
    project_id: str | None = None,
    unread_only: bool = False,
    limit: int = 50,
    before: str | None = None,
) -> tuple[list[dict[str, Any]], int, str | None]:
    """返回一页通知、未读总数和下一页 cursor。"""

    page_size = max(1, min(limit, 100))
    rows = list_user_notifications(
        project_id=project_id,
        unread_only=unread_only,
        limit=page_size + 1,
        before=before,
    )
    overflow = len(rows) > page_size
    items = rows[:page_size]
    next_before = items[-1]["created_at"] if overflow and items else None
    unread = count_unread_user_notifications(project_id=project_id)
    return items, unread, next_before


def unread_count(*, project_id: str | None = None) -> int:
    return count_unread_user_notifications(project_id=project_id)


def mark_read(note_id: str) -> dict[str, Any]:
    record = mark_user_notification_read(note_id)
    if record is None:
        raise NotificationNotFoundError(f"找不到通知: {note_id}")
    return record


def mark_all_read(*, project_id: str | None = None) -> int:
    return mark_all_user_notifications_read(project_id=project_id)


def get_notification(note_id: str) -> dict[str, Any] | None:
    return get_user_notification(note_id)
