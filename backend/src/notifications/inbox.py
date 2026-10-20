"""应用内通知 inbox：列表、已读和对外序列化。"""

from __future__ import annotations

from typing import Any

from src.notifications.hub import notification_hub
from src.storage.database import (
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
        "workspaceId": item["workspace_id"],
        "projectId": item["project_id"],
        "jobId": item["job_id"],
        "firingId": item["firing_id"],
        "ownerSessionId": str(item.get("owner_session_id") or ""),
        "sessionStatus": notification_session_status(str(item.get("owner_session_id") or "")),
        "kind": item["kind"],
        "title": item["title"],
        "body": item["body"],
        "payload": item.get("payload") or {},
        "readAt": item.get("read_at"),
        "createdAt": item["created_at"],
    }


def notification_session_status(session_id: str) -> str:
    """點擊前說明 owner Session 是可打開、已歸檔，還是已刪除。"""

    if not session_id:
        return ""
    from src.sessions.records import get_session

    session = get_session(session_id)
    if session is None:
        return "deleted"
    if str(session.get("status") or "") == "archived":
        return "archived"
    return "active"


def notification_click_target(item: dict[str, Any]) -> dict[str, str]:
    """點擊打開 owner Session。已刪除時不給出可導航 id。"""

    session_id = str(item.get("owner_session_id") or "")
    status = notification_session_status(session_id)
    if status == "deleted":
        return {"action": "unavailable", "reason": "deleted", "sessionId": session_id}
    if status == "archived":
        return {"action": "openSession", "sessionId": session_id, "status": "archived"}
    if status == "active":
        return {"action": "openSession", "sessionId": session_id, "status": "active"}
    return {"action": "legacy", "sessionId": "", "status": ""}


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
    project_id = str(record.get("project_id") or "")
    notification_hub.publish({
        "type": "read",
        "projectId": project_id,
        "unreadCount": unread_count(project_id=project_id or None),
        "notification": serialize_notification(record),
    })
    return record


def mark_all_read(*, project_id: str | None = None) -> int:
    updated = mark_all_user_notifications_read(project_id=project_id)
    notification_hub.publish({
        "type": "read_all",
        "projectId": project_id or "",
        "unreadCount": 0,
        "updatedCount": updated,
    })
    return updated


def get_notification(note_id: str) -> dict[str, Any] | None:
    return get_user_notification(note_id)
