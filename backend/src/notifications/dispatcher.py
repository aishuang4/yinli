"""通知分发器。按通道设置投递，单通道失败不影响其余通道。"""

from __future__ import annotations

import logging

from src.notifications.channels import iter_channels
from src.notifications.models import (
    ChannelDeliveryResult,
    DispatchResult,
    NotificationMessage,
)
from src.notifications.settings import is_channel_enabled

logger = logging.getLogger(__name__)


def dispatch_notification(message: NotificationMessage) -> DispatchResult:
    """将一条通知分发给已启用通道。

    应用内通道始终投递，作为产品 inbox 事实源。
    未实现或未启用的外部通道跳过，不阻断 firing 收尾。
    """

    if not str(message.kind).strip():
        raise ValueError("kind 不能为空")
    if not str(message.title).strip():
        raise ValueError("title 不能为空")
    if not str(message.workspace_id).strip():
        raise ValueError("workspace_id 不能为空")

    deliveries: list[ChannelDeliveryResult] = []
    in_app_record = None
    for channel in iter_channels():
        if channel.id != "in_app" and not is_channel_enabled(channel.id):
            continue
        if channel.id != "in_app" and not channel.implemented:
            deliveries.append(ChannelDeliveryResult(
                channel_id=channel.id,
                ok=False,
                skipped=True,
                reason="not_implemented",
            ))
            continue
        try:
            result = channel.deliver(message)
        except Exception:
            logger.exception("通知通道投递失败 channel=%s kind=%s", channel.id, message.kind)
            result = ChannelDeliveryResult(
                channel_id=channel.id,
                ok=False,
                reason="deliver_error",
            )
        deliveries.append(result)
        if channel.id == "in_app" and result.record:
            in_app_record = result.record
    return DispatchResult(
        message=message,
        in_app_record=in_app_record,
        deliveries=deliveries,
    )
