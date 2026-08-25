"""通知通道协议。后续邮箱 / 微信 / 飞书实现只需实现本接口并登记。"""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.notifications.models import ChannelDeliveryResult, NotificationMessage


class NotificationChannel(ABC):
    """通知投递通道。

    分发器只调用 `deliver()`，不关心通道内部是写 SQLite、发 HTTP 还是调 SDK。
    未实现的通道必须返回 `skipped=True`，不得抛未捕获异常阻断其他通道。
    """

    id: str
    display_name: str
    implemented: bool = False

    @abstractmethod
    def deliver(self, message: NotificationMessage) -> ChannelDeliveryResult:
        """投递一条通知。"""
