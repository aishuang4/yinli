"""Team Run、Member、Task、Activity 与 Mail 的独立 Block projector。"""

from ..domain_events import DomainEvent
from ..frame_schema import DisplaySnapshot
from .common import project_kind_lifecycle

EVENT_TYPES = {
    f"{prefix}.{suffix}"
    for prefix in ("team", "team.member", "team.task", "team.activity", "team.mail")
    for suffix in ("started", "delta", "updated", "completed", "failed", "cancelled")
}

_KIND_BY_PREFIX = {
    "team": "team",
    "team.member": "team-member",
    "team.task": "team-task",
    "team.activity": "team-activity",
    "team.mail": "team-mail",
}


def project(event: DomainEvent, snapshot: DisplaySnapshot):
    prefix = event.type.rsplit(".", 1)[0]
    kind = _KIND_BY_PREFIX.get(prefix)
    if kind is None:
        return []
    return project_kind_lifecycle(event, kind=kind)
