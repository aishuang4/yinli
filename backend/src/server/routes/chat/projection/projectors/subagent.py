"""Subagent 摘要 Block projector，不包含完整 transcript。"""

from ..domain_events import DomainEvent
from ..frame_schema import DisplaySnapshot
from .common import project_kind_lifecycle

EVENT_TYPES = {"subagent.started", "subagent.updated", "subagent.completed", "subagent.failed", "subagent.cancelled"}


def project(event: DomainEvent, snapshot: DisplaySnapshot):
    return project_kind_lifecycle(event, kind="subagent")
