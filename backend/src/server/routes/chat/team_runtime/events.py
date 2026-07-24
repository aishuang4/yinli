"""Team Runtime 进程内源信号封装。"""

from src.server.routes.chat.helpers import source_event
from src.server.routes.chat.projection.source_events import SourceEvent


def team_source_event(event: dict) -> SourceEvent:
    """把 Team domain event 包装为进程内源信号。"""

    return source_event("TEAM_DOMAIN_EVENT", event)
