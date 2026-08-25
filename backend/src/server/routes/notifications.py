"""应用内通知 REST。不绑定 Chat SSE。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from src.scheduler.notifications import (
    NotificationNotFoundError,
    list_notification_page,
    mark_all_read,
    mark_read,
    unread_count as count_unread,
)

router = APIRouter()


def serialize_notification(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item["id"],
        "workspaceId": item["workspace_id"],
        "projectId": item["project_id"],
        "jobId": item["job_id"],
        "firingId": item["firing_id"],
        "kind": item["kind"],
        "title": item["title"],
        "body": item["body"],
        "payload": item.get("payload") or {},
        "readAt": item.get("read_at"),
        "createdAt": item["created_at"],
    }


@router.get("/notifications/unread-count")
async def get_unread_count(
    project_id: str | None = Query(default=None, alias="projectId"),
) -> dict[str, int]:
    """未读通知条数。"""

    return {"unreadCount": count_unread(project_id=project_id)}


@router.post("/notifications/read-all")
async def read_all_notifications(
    project_id: str | None = Query(default=None, alias="projectId"),
) -> dict[str, int]:
    """将未读通知全部标为已读。"""

    return {"updatedCount": mark_all_read(project_id=project_id)}


@router.get("/notifications")
async def list_notifications(
    project_id: str | None = Query(default=None, alias="projectId"),
    unread_only: bool = Query(default=False, alias="unreadOnly"),
    limit: int = Query(default=50, ge=1, le=100),
    before: str | None = Query(default=None),
) -> dict[str, Any]:
    """列出通知；limit 默认 50。"""

    items, unread, next_before = list_notification_page(
        project_id=project_id,
        unread_only=unread_only,
        limit=limit,
        before=before,
    )
    return {
        "items": [serialize_notification(item) for item in items],
        "unreadCount": unread,
        "nextBefore": next_before,
    }


@router.post("/notifications/{notification_id}/read")
async def read_notification(notification_id: str) -> dict[str, Any]:
    """将一条通知标为已读。"""

    try:
        record = mark_read(notification_id)
    except NotificationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return serialize_notification(record)
