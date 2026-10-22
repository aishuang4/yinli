"""系统级硬规则。不进群、不调用模型。"""

from __future__ import annotations

from src.group_studio.errors import (
    ILLEGAL_TRANSITION,
    STUDIO_NOT_OPEN,
    STUDIO_PAUSED,
    GroupStudioError,
)
from src.group_studio.settings import DEFAULT_MAX_MEMBERS, get_group_studio_settings_public

MUTATION_BLOCKED = {
    "done": STUDIO_NOT_OPEN,
    "archived": STUDIO_NOT_OPEN,
    "deleted": ILLEGAL_TRANSITION,
    "failed": STUDIO_NOT_OPEN,
}


def max_members() -> int:
    """单群 Worker 人数硬顶。"""

    return int(get_group_studio_settings_public().get("maxMembers") or DEFAULT_MAX_MEMBERS)


def assert_can_mutate(status: str) -> None:
    """结束/删除的房间不能改成员或置顶。paused 仍可拉人。"""

    code = MUTATION_BLOCKED.get(status)
    if code is not None:
        raise GroupStudioError(409, code)


def assert_floor_allowed(status: str, *, role: str = "worker") -> None:
    """只有 open 能生成。"""

    if status == "paused":
        raise GroupStudioError(409, STUDIO_PAUSED)
    if status != "open":
        raise GroupStudioError(409, STUDIO_NOT_OPEN if status in {"done", "deleted"} else ILLEGAL_TRANSITION)
    if role == "host":
        raise GroupStudioError(409, ILLEGAL_TRANSITION)


def assert_serial_run(current_run_id: str | None) -> None:
    """同时只能有一条 GroupRun。"""

    if current_run_id:
        raise GroupStudioError(409, ILLEGAL_TRANSITION)
