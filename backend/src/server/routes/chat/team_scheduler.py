"""Team Worker 依赖调度纯逻辑。

本模块不创建 Agent、不消费事件，只判断 Worker 当前是否可运行、应跳过、
还是继续等待。这样 Team Runtime 后续切换到 AgentScope message bus 时，
调度状态机可以保持稳定。
"""

from dataclasses import dataclass, field
from typing import Literal

from src.server.routes.chat.models import ChatTeamMemberPayload
from src.server.routes.chat.team_state import _member_dependency_ids


WorkerScheduleStatus = Literal["ready", "waiting", "invalid_dependency", "upstream_failed"]


@dataclass
class TeamSchedulerState:
    """Team 调度器在一次运行中的内存状态。"""

    pending: dict[str, ChatTeamMemberPayload]
    all_worker_ids: set[str]
    completed_outputs: dict[str, dict[str, str]] = field(default_factory=dict)
    failed_workers: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class WorkerScheduleDecision:
    """单个 Worker 的调度决策。"""

    status: WorkerScheduleStatus
    dependencies: list[str] = field(default_factory=list)
    dependency_outputs: list[dict[str, str]] = field(default_factory=list)


def decide_worker_schedule(
    *,
    member: ChatTeamMemberPayload,
    workers: list[ChatTeamMemberPayload],
    state: TeamSchedulerState,
) -> WorkerScheduleDecision:
    """判断 Worker 当前是否可以启动。"""

    dependencies = _member_dependency_ids(member, workers)
    invalid_dependencies = [dep for dep in dependencies if dep not in state.all_worker_ids]
    if invalid_dependencies:
        return WorkerScheduleDecision(
            status="invalid_dependency",
            dependencies=invalid_dependencies,
        )

    failed_dependencies = [dep for dep in dependencies if dep in state.failed_workers]
    if failed_dependencies:
        return WorkerScheduleDecision(
            status="upstream_failed",
            dependencies=failed_dependencies,
        )

    blocking = [
        dep
        for dep in dependencies
        if dep not in state.completed_outputs and dep not in state.failed_workers
    ]
    if blocking:
        return WorkerScheduleDecision(
            status="waiting",
            dependencies=blocking,
        )

    return WorkerScheduleDecision(
        status="ready",
        dependencies=dependencies,
        dependency_outputs=[
            state.completed_outputs[dep]
            for dep in dependencies
            if dep in state.completed_outputs
        ],
    )
