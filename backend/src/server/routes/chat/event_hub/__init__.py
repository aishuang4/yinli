"""Chat Event Hub 中间层。

该包把 Runtime 原始 SSE 事件投影成前端可恢复的 RunState。
"""

from src.server.routes.chat.event_hub.projector import initial_run_state, normalize_run_state, project_event

__all__ = ["initial_run_state", "normalize_run_state", "project_event"]
