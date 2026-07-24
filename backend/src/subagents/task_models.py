"""Subagent 持久任务的状态模型与迁移规则。"""

from __future__ import annotations

from typing import Literal

SubagentTaskStatus = Literal[
    "queued", "running", "waiting", "completed", "failed", "cancelled", "interrupted",
]

TERMINAL_SUBAGENT_TASK_STATUSES = {"completed", "failed", "cancelled"}

ALLOWED_SUBAGENT_TASK_TRANSITIONS: dict[str, set[str]] = {
    "queued": {"running", "cancelled"},
    "running": {"waiting", "completed", "failed", "cancelled", "interrupted"},
    "waiting": {"running", "cancelled", "interrupted"},
    "interrupted": {"running", "cancelled"},
    "completed": set(),
    "failed": set(),
    "cancelled": set(),
}


def can_transition_subagent_task(current: str, target: str) -> bool:
    """判断一次 Subagent task 状态迁移是否合法。"""

    return target in ALLOWED_SUBAGENT_TASK_TRANSITIONS.get(current, set())
