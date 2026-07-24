"""Plan Block projector。"""

from ..domain_events import DomainEvent
from ..frame_schema import DisplaySnapshot
from .common import project_kind_lifecycle

EVENT_TYPES = {"plan.started", "plan.updated", "plan.completed", "plan.failed", "plan.cancelled"}


def project(event: DomainEvent, snapshot: DisplaySnapshot):
    return project_kind_lifecycle(event, kind="plan")
