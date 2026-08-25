"""已登记的通知通道。新增实现时在此注册，分发器按 id 查找。"""

from __future__ import annotations

from src.notifications.channels.external import (
    EmailNotificationChannel,
    FeishuNotificationChannel,
    WeChatNotificationChannel,
)
from src.notifications.channels.in_app import InAppNotificationChannel
from src.notifications.protocol import NotificationChannel

_CHANNEL_INSTANCES: tuple[NotificationChannel, ...] = (
    InAppNotificationChannel(),
    EmailNotificationChannel(),
    WeChatNotificationChannel(),
    FeishuNotificationChannel(),
)

CHANNEL_REGISTRY: dict[str, NotificationChannel] = {
    channel.id: channel for channel in _CHANNEL_INSTANCES
}


def iter_channels() -> tuple[NotificationChannel, ...]:
    """按登记顺序返回通道实例。"""

    return _CHANNEL_INSTANCES
