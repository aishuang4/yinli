"""Team Runtime 独立 SSE 事件封装。"""

from src.server.routes.chat.helpers import _sse_event


def team_event_sse(event: dict) -> str:
    """把 Team domain event 包装为顶层 TEAM_EVENT。"""

    return _sse_event("TEAM_EVENT", event)
