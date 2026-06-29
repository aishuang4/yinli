"""Team 调度器回归测试。"""

from src.server.routes.chat.models import ChatTeamMemberPayload
from src.server.routes.chat.team_assignment_planner import parse_team_assignment_json
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


def test_parse_team_assignment_json_filters_unknown_agents() -> None:
    """Planner JSON 只能分派给本轮允许的 Worker。"""

    text = """
    ```json
    {
      "summary": "检查 Chat 链路",
      "assignments": [
        {
          "agentId": "research-worker",
          "task": "梳理用户视角流程",
          "expectedOutput": "流程清单",
          "constraints": "只报告发现",
          "dependsOn": []
        },
        {
          "agentId": "unknown-worker",
          "task": "不应保留"
        }
      ]
    }
    ```
    """

    assignments = parse_team_assignment_json(text, {"research-worker"})

    assert list(assignments) == ["research-worker"]
    assert assignments["research-worker"].task == "梳理用户视角流程"
    assert assignments["research-worker"].expected_output == "流程清单"
    assert assignments["research-worker"].constraints == "只报告发现"


def test_parse_team_assignment_json_returns_empty_for_invalid_text() -> None:
    """Planner 输出不是 JSON 时交给调用方走兜底分派。"""

    assert parse_team_assignment_json("我无法处理这个请求", {"worker-a"}) == {}
