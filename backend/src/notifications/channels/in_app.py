"""应用内通知通道：写入 SQLite 并向订阅中心推送。"""

from __future__ import annotations

from src.notifications.hub import notification_hub
from src.notifications.inbox import serialize_notification, unread_count
from src.notifications.models import ChannelDeliveryResult, NotificationMessage
from src.notifications.protocol import NotificationChannel
from src.storage.database import insert_user_notification


class InAppNotificationChannel(NotificationChannel):
    """产品内 inbox。始终作为系统事实源落库。"""

    id = "in_app"
    display_name = "应用内通知"
    implemented = True

    def deliver(self, message: NotificationMessage) -> ChannelDeliveryResult:
        record = insert_user_notification({
            "workspace_id": message.workspace_id,
            "project_id": message.project_id,
            "job_id": message.job_id,
            "firing_id": message.firing_id,
            "kind": message.kind,
            "title": message.title,
            "body": message.body,
            "payload": dict(message.payload),
        })
        project_id = str(record.get("project_id") or "") or None
        notification_hub.publish({
            "type": "notification",
            "projectId": record.get("project_id") or "",
            "unreadCount": unread_count(project_id=project_id),
            "notification": serialize_notification(record),
        })
        return ChannelDeliveryResult(
            channel_id=self.id,
            ok=True,
            record=record,
        )
