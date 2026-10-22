"""应用内通知 inbox：列表、已读和对外序列化。"""
from __future__ import annotations


from typing import Any

from src.notifications.hub import notification_hub
from src.storage.records.scheduled_job_records import (
    count_unread_user_notifications,
    get_user_notification,
    list_user_notifications,
    mark_all_user_notifications_read,
    mark_user_notification_read,
)


class NotificationNotFoundError(Exception):
    """通知不存在。"""


def serialize_notification(item: dict[str, Any]) -> dict[str, Any]:
    """将存储记录转为前端 camelCase。"""

    return {
        "id": item["id"],
        "tenantId": item.get("tenant_id") or item.get("tenantId") or "",
        "projectSlug": item.get("project_slug") or item.get("projectSlug") or "",
        "jobId": item["job_id"],
        "firingId": item["firing_id"],
        "kind": item["kind"],
        "title": item["title"],
        "body": item["body"],
        "payload": item.get("payload") or {},
        "readAt": item.get("read_at"),
        "createdAt": item["created_at"],
    }


def list_notification_page(
    *,
    project_slug: str | None = None,
    unread_only: bool = False,
    limit: int = 50,
    before: str | None = None,
) -> tuple[list[dict[str, Any]], int, str | None]:
    """返回一页通知、未读总数和下一页 cursor。"""

    page_size = max(1, min(limit, 100))
    rows = list_user_notifications(
        project_slug=project_slug,
        unread_only=unread_only,
        limit=page_size + 1,
        before=before,
    )
    overflow = len(rows) > page_size
    items = rows[:page_size]
    next_before = items[-1]["created_at"] if overflow and items else None
    unread = count_unread_user_notifications(project_slug=project_slug)
    return items, unread, next_before


def unread_count(*, project_slug: str | None = None) -> int:
    return count_unread_user_notifications(project_slug=project_slug)


def mark_read(note_id: str) -> dict[str, Any]:
    record = mark_user_notification_read(note_id)
    if record is None:
        raise NotificationNotFoundError(f"找不到通知: {note_id}")
    project_slug = str(record.get("project_slug") or "")
    notification_hub.publish({
        "type": "read",
        "projectSlug": project_slug,
        "unreadCount": unread_count(project_slug=project_slug or None),
        "notification": serialize_notification(record),
    })
    return record


def mark_all_read(*, project_slug: str | None = None) -> int:
    updated = mark_all_user_notifications_read(project_slug=project_slug)
    notification_hub.publish({
        "type": "read_all",
        "projectSlug": project_slug or "",
        "unreadCount": 0,
        "updatedCount": updated,
    })
    return updated


def get_notification(note_id: str) -> dict[str, Any] | None:
    return get_user_notification(note_id)
