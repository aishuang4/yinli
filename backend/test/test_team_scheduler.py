"""Team 调度器回归测试。"""

from src.server.routes.chat.models import ChatTeamMemberPayload
from src.server.routes.chat.team_scheduler import TeamSchedulerState, decide_worker_schedule


def test_dependency_on_running_worker_waits_instead_of_invalid() -> None:
    """下游依赖正在运行的 Worker 时应等待，而不是被判定为无效依赖。"""

    upstream = ChatTeamMemberPayload(
        agentId="worker-a",
        name="Worker A",
        role="worker",
    )
    downstream = ChatTeamMemberPayload(
        agentId="worker-b",
        name="Worker B",
        role="worker",
        executionMode="depends_on",
        dependsOn=["worker-a"],
    )
    state = TeamSchedulerState(
        pending={"worker-b": downstream},
        all_worker_ids={"worker-a", "worker-b"},
    )

    decision = decide_worker_schedule(
        member=downstream,
        workers=[upstream, downstream],
        state=state,
    )

    assert decision.status == "waiting"
    assert decision.dependencies == ["worker-a"]


def test_dependency_on_unknown_worker_is_invalid() -> None:
    """依赖不存在的 Worker 时应明确失败。"""

    downstream = ChatTeamMemberPayload(
        agentId="worker-b",
        name="Worker B",
        role="worker",
        executionMode="depends_on",
        dependsOn=["missing-worker"],
    )
    state = TeamSchedulerState(
        pending={"worker-b": downstream},
        all_worker_ids={"worker-b"},
    )

    decision = decide_worker_schedule(
        member=downstream,
        workers=[downstream],
        state=state,
    )

    assert decision.status == "invalid_dependency"
    assert decision.dependencies == ["missing-worker"]
