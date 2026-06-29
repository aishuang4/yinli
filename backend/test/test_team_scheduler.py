"""Team 调度器回归测试。"""

from src.server.routes.chat.models import ChatTeamActivity, ChatTeamMemberPayload
from src.server.routes.chat.team_assignment_planner import parse_team_assignment_json
from src.server.routes.chat.team_report_reviewer import (
    _fallback_reviews,
    parse_team_report_review_json,
)
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


def test_parse_team_report_review_json_filters_and_normalizes() -> None:
    """Report Reviewer JSON 只保留允许 Worker，并规范状态值。"""

    text = """
    {
      "overallStatus": "insufficient",
      "reviews": [
        {"agentId": "worker-a", "status": "accepted", "gaps": [], "notes": "可用"},
        {"agentId": "worker-b", "status": "bad", "gaps": ["缺少证据"], "notes": "需要补充"},
        {"agentId": "unknown", "status": "accepted"}
      ]
    }
    """

    reviews = parse_team_report_review_json(text, {"worker-a", "worker-b"})

    assert reviews["worker-a"].status == "accepted"
    assert reviews["worker-b"].status == "needs_revision"
    assert reviews["worker-b"].gaps == ("缺少证据",)
    assert "unknown" not in reviews


def test_fallback_report_review_marks_short_report_as_needs_revision() -> None:
    """模型 reviewer 不可用时，过短 report 会被标记为需要补充。"""

    reviews = _fallback_reviews([
        ChatTeamActivity(
            id="activity-worker",
            agentId="worker-a",
            agentName="Worker A",
            role="worker",
            status="completed",
            task="检查实现",
            report="太短",
            expectedOutput="实现链路、风险和证据",
            createdAt="2026-06-29T00:00:00+00:00",
        ),
    ])

    assert reviews["worker-a"].status == "needs_revision"
    assert reviews["worker-a"].gaps
