"""执行层可见的精确行动约束，不把框架类型或原始凭据传给工具。"""

from contextlib import contextmanager
from contextvars import ContextVar

from .models import ActionSnapshot

_EXECUTING_ACTION: ContextVar[ActionSnapshot | None] = ContextVar("platform_executing_action", default=None)


def has_authorized_action() -> bool:
    """出站客户端据此禁止未经独立快照授权的自动跳转。"""
    return _EXECUTING_ACTION.get() is not None


@contextmanager
def execution_scope(action: ActionSnapshot):
    """并发工具分别绑定自己的平台快照，finally 恢复父执行上下文。"""
    token = _EXECUTING_ACTION.set(action)
    try:
        yield
    finally:
        _EXECUTING_ACTION.reset(token)


def validate_fetch_redirect(current_url: str, next_url: str) -> None:
    """只读豁免只覆盖已经明确的 URL，重定向不得携带新 query 外发。"""
    action = _EXECUTING_ACTION.get()
    if action is not None and action.tool_name == "WebFetch" and action.effect == "read_only":
        if next_url != current_url:
            raise ValueError("网页重定向需要对新目标重新授权")
