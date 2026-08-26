"""系统级调度台。不进群、不调用模型；只做阶段机与人数/发言权硬规则。"""

from __future__ import annotations

from src.group_studio.errors import (
    DRAFTING_NO_FLOOR,
    GOAL_REQUIRED,
    ILLEGAL_TRANSITION,
    STUDIO_CIRCUIT_BROKEN,
    STUDIO_PAUSED,
    GroupStudioError,
)
from src.group_studio.settings import DEFAULT_MAX_MEMBERS, get_group_studio_settings_public

FLOOR_ALLOWED_STATUSES = frozenset({"discussing", "executing", "reviewing"})
MUTATION_BLOCKED = {
    "paused": STUDIO_PAUSED,
    "circuit_broken": STUDIO_CIRCUIT_BROKEN,
    "archived": ILLEGAL_TRANSITION,
    "failed": ILLEGAL_TRANSITION,
}


def max_members() -> int:
    """单群人数硬顶（含 Host）。"""

    return int(get_group_studio_settings_public().get("maxMembers") or DEFAULT_MAX_MEMBERS)


def assert_can_mutate(status: str) -> None:
    """暂停/熔断/终态不得推进任务卡或加人。"""

    code = MUTATION_BLOCKED.get(status)
    if code is not None:
        raise GroupStudioError(409, code)


def assert_floor_allowed(status: str, *, role: str = "host") -> None:
    """drafting / paused / 熔断禁止发言。awaiting_human 只允许 Host 复述。"""

    if status == "drafting":
        raise GroupStudioError(409, DRAFTING_NO_FLOOR)
    if status == "paused":
        raise GroupStudioError(409, STUDIO_PAUSED)
    if status == "circuit_broken":
        raise GroupStudioError(409, STUDIO_CIRCUIT_BROKEN)
    if status == "awaiting_human":
        if role != "host":
            raise GroupStudioError(409, ILLEGAL_TRANSITION)
        return
    if status not in FLOOR_ALLOWED_STATUSES:
        raise GroupStudioError(409, ILLEGAL_TRANSITION)


def assert_can_start_execution(status: str, goal: str) -> None:
    """人类或 Host 进入执行期的守卫。"""

    assert_floor_allowed(status)
    if status != "discussing":
        raise GroupStudioError(409, ILLEGAL_TRANSITION)
    if not goal.strip():
        raise GroupStudioError(422, GOAL_REQUIRED)


def assert_serial_run(current_run_id: str | None) -> None:
    """P0 同时只能有一条 GroupRun。"""

    if current_run_id:
        raise GroupStudioError(409, ILLEGAL_TRANSITION)
