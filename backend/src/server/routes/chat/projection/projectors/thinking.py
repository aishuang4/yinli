"""Thinking Block projector。"""

from ..domain_events import DomainEvent
from ..frame_schema import DisplaySnapshot
from .common import project_kind_lifecycle

EVENT_TYPES = {"thinking.started", "thinking.delta", "thinking.completed", "thinking.cancelled"}


def project(event: DomainEvent, snapshot: DisplaySnapshot):
    return project_kind_lifecycle(event, kind="thinking")
