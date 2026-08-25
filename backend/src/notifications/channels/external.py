"""外部通知通道占位。接入邮箱 / 微信 / 飞书时替换 deliver()，不要改分发器。"""

from __future__ import annotations

from src.notifications.models import ChannelDeliveryResult, NotificationMessage
from src.notifications.protocol import NotificationChannel


class UnimplementedNotificationChannel(NotificationChannel):
    """尚未实现的外部通道。启用前设置接口会拒绝，这里再兜底跳过。"""

    implemented = False

    def deliver(self, message: NotificationMessage) -> ChannelDeliveryResult:
        del message
        return ChannelDeliveryResult(
            channel_id=self.id,
            ok=False,
            skipped=True,
            reason="not_implemented",
        )


class EmailNotificationChannel(UnimplementedNotificationChannel):
    """邮箱通知占位。"""

    id = "email"
    display_name = "邮箱"


class WeChatNotificationChannel(UnimplementedNotificationChannel):
    """微信通知占位。"""

    id = "wechat"
    display_name = "微信"


class FeishuNotificationChannel(UnimplementedNotificationChannel):
    """飞书通知占位。"""

    id = "feishu"
    display_name = "飞书"
