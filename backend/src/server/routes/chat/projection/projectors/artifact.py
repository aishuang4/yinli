"""Artifact/files Block projector。"""

from ..domain_events import DomainEvent
from ..frame_schema import DisplaySnapshot
from .common import project_kind_lifecycle

EVENT_TYPES = {"artifact.started", "artifact.updated", "artifact.completed", "artifact.failed", "artifact.cancelled"}


def project(event: DomainEvent, snapshot: DisplaySnapshot):
    return project_kind_lifecycle(event, kind="artifact")
