"""通知子系统。通道协议与分发器是稳定入口，inbox 只服务应用内通知。"""

from src.notifications.dispatcher import dispatch_notification
from src.notifications.models import NotificationMessage

__all__ = ["dispatch_notification", "NotificationMessage"]
