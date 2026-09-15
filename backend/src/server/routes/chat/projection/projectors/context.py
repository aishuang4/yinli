"""上下文占用 Block projector；detached，Chat 顶栏读取，消息树不渲染。"""

from ..domain_events import DomainEvent
from ..frame_schema import DisplaySnapshot
from .common import project_kind_lifecycle

EVENT_TYPES = {"context.started", "context.updated", "context.completed", "context.failed", "context.cancelled"}


def project(event: DomainEvent, snapshot: DisplaySnapshot):
    return project_kind_lifecycle(event, kind="context")
