"""Team 摘要 Block projector，完整协作状态仍由 kind data 自己解释。"""

from ..domain_events import DomainEvent
from ..frame_schema import DisplaySnapshot
from .common import project_kind_lifecycle

EVENT_TYPES = {"team.started", "team.updated", "team.completed", "team.failed", "team.cancelled"}


def project(event: DomainEvent, snapshot: DisplaySnapshot):
    return project_kind_lifecycle(event, kind="team")
