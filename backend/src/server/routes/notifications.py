"""通知 REST 与独立 SSE。不绑定 Chat SSE。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from src.notifications.inbox import (
    NotificationNotFoundError,
    list_notification_page,
    mark_all_read,
    mark_read,
    serialize_notification,
    unread_count as count_unread,
)
from src.notifications.settings import (
    NotificationSettingsError,
    list_channel_settings,
    save_channel_settings,
)
from src.notifications.stream import notification_event_stream

router = APIRouter()


class ChannelSettingsPatch(BaseModel):
    """单个通道的设置补丁。"""

    id: str = Field(..., min_length=1)
    enabled: bool | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class NotificationSettingsUpdate(BaseModel):
    """通知通道设置更新。"""

    channels: list[ChannelSettingsPatch] = Field(default_factory=list)


@router.get("/notifications/unread-count")
async def get_unread_count(
    project_id: str | None = Query(default=None, alias="projectId"),
) -> dict[str, int]:
    """未读通知条数。"""

    return {"unreadCount": count_unread(project_id=project_id)}


@router.get("/notifications/settings")
async def get_notification_settings() -> dict[str, Any]:
    """通知通道目录与当前配置。"""

    return {"channels": list_channel_settings()}


@router.put("/notifications/settings")
async def update_notification_settings(data: NotificationSettingsUpdate) -> dict[str, Any]:
    """保存通知通道开关与配置。未实现通道不能启用。"""

    try:
        channels = save_channel_settings([item.model_dump() for item in data.channels])
    except NotificationSettingsError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"channels": channels}


@router.get("/notifications/stream")
async def stream_notifications(
    project_id: str | None = Query(default=None, alias="projectId"),
) -> StreamingResponse:
    """订阅应用内通知推送。独立于 Chat SSE。"""

    return StreamingResponse(
        notification_event_stream(project_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


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
