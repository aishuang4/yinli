"""定时任务调用 Chat 的公开入口。

只提供建立排程 Run、核对工作目录、读取终态、取消和对账。
调度器不要再 import routes.chat。
"""

from src.server.routes.chat.background_runs import (
    cancel_chat_background_run,
    get_chat_background_run,
    reconcile_interrupted_chat_runs,
)
from src.server.routes.chat.lifecycle import TERMINAL_RUN_STATUSES
from src.server.routes.chat.scheduled_launch import (
    assert_job_workspace_in_scope,
    launch_scheduled_chat_run,
)

__all__ = [
    "TERMINAL_RUN_STATUSES",
    "assert_job_workspace_in_scope",
    "cancel_chat_background_run",
    "get_chat_background_run",
    "launch_scheduled_chat_run",
    "reconcile_interrupted_chat_runs",
]
