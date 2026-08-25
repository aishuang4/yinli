"""通知领域模型。通道实现只依赖本模块，不依赖路由或 Chat SSE。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ChannelConfigField:
    """通道配置字段，供设置页按目录渲染。"""

    key: str
    label: str
    input: str = "text"
    placeholder: str = ""


@dataclass(frozen=True)
class ChannelSpec:
    """通道目录项。新增通道时先登记这里，再补实现。"""

    id: str
    display_name: str
    description: str
    implemented: bool
    always_on: bool = False
    config_fields: tuple[ChannelConfigField, ...] = ()


@dataclass
class NotificationMessage:
    """跨通道统一消息。通道只消费该结构，不解析 Job / Run 对象。"""

    kind: str
    title: str
    workspace_id: str
    project_id: str = ""
    job_id: str = ""
    firing_id: str = ""
    body: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    source: str = "system"


@dataclass(frozen=True)
class ChannelDeliveryResult:
    """单通道投递结果。skipped 表示未实现或未启用。"""

    channel_id: str
    ok: bool
    skipped: bool = False
    reason: str = ""
    record: dict[str, Any] | None = None


@dataclass
class DispatchResult:
    """一次分发的汇总，供控制面与测试断言。"""

    message: NotificationMessage
    in_app_record: dict[str, Any] | None
    deliveries: list[ChannelDeliveryResult]
