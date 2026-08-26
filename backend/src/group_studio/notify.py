"""工作室通知。走 notifications 分发器，不写 Chat SSE。"""

from __future__ import annotations

from typing import Any

from src.notifications import NotificationMessage, dispatch_notification

KIND_CIRCUIT = "group_studio_circuit"
KIND_AWAITING = "group_studio_awaiting_human"
KIND_COMPLETED = "group_studio_completed"
KIND_FAILED = "group_studio_failed"

_TITLES = {
    KIND_CIRCUIT: "工作室已熔断",
    KIND_AWAITING: "工作室等待你确认",
    KIND_COMPLETED: "工作室已完成",
    KIND_FAILED: "工作室失败",
}


def notify_studio(
    snapshot: dict[str, Any],
    *,
    kind: str,
    body: str = "",
) -> None:
    """投递一条群聊工作室通知。"""

    title = _TITLES.get(kind, "工作室通知")
    dispatch_notification(NotificationMessage(
        kind=kind,
        title=title,
        workspace_id=str(snapshot.get("workspaceId") or ""),
        project_id=str(snapshot.get("projectId") or ""),
        body=body or str(snapshot.get("title") or ""),
        payload={
            "groupStudioId": snapshot.get("id") or "",
            "chatSessionId": snapshot.get("chatSessionId") or "",
        },
        source="group_studio",
    ))
