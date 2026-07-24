import asyncio
import json
import uuid

from agentscope.message import ToolResultState

from src.server.routes.chat.plan_mode.manager import PlanModeManager
from src.server.routes.chat.plan_mode.tools import (
    PlanCompleteTool,
    PlanEnterTool,
    PlanRequestReviewTool,
    PlanSetStepsTool,
    PlanWriteTool,
    TaskOutputTool,
)


def _manager(*, persist: bool = False, session_id: str = "session-plan") -> PlanModeManager:
    return PlanModeManager(
        session_id=session_id,
        run_id=f"run-{uuid.uuid4().hex}",
        agent_id="agent-plan",
        agent_name="Agent",
        persist=persist,
        requested=True,
    )


def _draft(manager: PlanModeManager) -> None:
    manager.enter(goal="实现可审核计划", title="Plan Review")
    manager.write(markdown="# Plan Review\n\n按步骤执行。", source="test")
    manager.write_tasks(items=[
        {"title": "核对架构", "description": "读取现有实现。"},
        {"title": "实现并验证", "description": "修改代码并运行测试。"},
    ])


def test_manager_drafts_and_requests_review() -> None:
    manager = _manager()
    _draft(manager)

    reviewed = manager.request_review()

    assert reviewed.plan_active is False
    assert reviewed.status == "review_waiting"
    assert reviewed.revision == 1
    assert [event["type"] for event in manager.drain_runtime_events()] == [
        "PLAN_CREATED",
        "PLAN_CONTENT_REPLACED",
        "PLAN_STEPS_REPLACED",
        "PLAN_REVIEW_REQUESTED",
    ]


def test_review_revision_and_execution_are_restored_from_storage() -> None:
    session_id = f"session-{uuid.uuid4().hex}"
    manager = _manager(persist=True, session_id=session_id)
    _draft(manager)
    plan = manager.request_review()

    revising = PlanModeManager(
        session_id=session_id,
        run_id=f"run-{uuid.uuid4().hex}",
        agent_id="agent-plan",
        agent_name="Agent",
        persist=True,
        plan_id=plan.plan_id,
        action="revise",
        feedback="把验证拆成独立步骤",
    )
    assert revising.context.status == "revising"
    assert revising.context.plan_active is True
    revising.write(markdown="# Plan Review v2\n\n新增独立验证。", source="tool")
    revising.write_tasks(items=[
        {"title": "核对架构"},
        {"title": "实现"},
        {"title": "验证"},
    ])
    revised = revising.request_review()
    assert revised.revision == 2

    executing = PlanModeManager(
        session_id=session_id,
        run_id=f"run-{uuid.uuid4().hex}",
        agent_id="agent-plan",
        agent_name="Agent",
        persist=True,
        plan_id=plan.plan_id,
        action="approve",
    )
    assert executing.context.status == "executing"
    assert executing.context.approved_revision == 2
    assert [event["type"] for event in executing.drain_runtime_events()] == [
        "PLAN_APPROVED",
        "PLAN_EXECUTION_STARTED",
    ]


def test_execution_steps_stream_and_complete() -> None:
    session_id = f"session-{uuid.uuid4().hex}"
    manager = _manager(persist=True, session_id=session_id)
    _draft(manager)
    plan = manager.request_review()
    executing = PlanModeManager(
        session_id=session_id,
        run_id=f"run-{uuid.uuid4().hex}",
        agent_id="agent-plan",
        agent_name="Agent",
        persist=True,
        plan_id=plan.plan_id,
        action="approve",
    )
    executing.drain_runtime_events()
    for index in range(2):
        executing.record_task_output(step_index=index, status="running")
        executing.record_task_output(
            step_index=index,
            status="completed",
            evidence=f"evidence-{index}",
            result=f"result-{index}",
        )
    completed = executing.complete_execution(summary="全部完成")

    assert completed.status == "completed"
    events = executing.drain_runtime_events()
    assert [event["type"] for event in events].count("PLAN_STEP_UPDATED") == 4
    assert events[-1]["type"] == "PLAN_COMPLETED"


def test_plan_tools_return_new_contract_payloads() -> None:
    manager = _manager()
    assert asyncio.run(PlanEnterTool(manager)(goal="制定计划")).state == ToolResultState.SUCCESS
    asyncio.run(PlanWriteTool(manager)(markdown="# Plan\n\ncontent"))
    steps_result = asyncio.run(PlanSetStepsTool(manager)(items=[{"title": "步骤一"}]))
    assert json.loads(steps_result.content[0].text)["steps"][0]["status"] == "pending"
    review_result = asyncio.run(PlanRequestReviewTool(manager)())
    assert json.loads(review_result.content[0].text)["status"] == "review_waiting"


def test_execution_tools_require_approved_plan() -> None:
    manager = _manager()
    _draft(manager)
    try:
        asyncio.run(TaskOutputTool(manager)(stepIndex=0, status="running"))
    except ValueError as exc:
        assert "approved plan" in str(exc)
    else:
        raise AssertionError("task_output should reject an unapproved plan")

    try:
        asyncio.run(PlanCompleteTool(manager)(summary="too early"))
    except ValueError as exc:
        assert "approved plan" in str(exc)
    else:
        raise AssertionError("plan_complete should reject an unapproved plan")


def test_incomplete_draft_run_is_failed() -> None:
    manager = _manager()
    _draft(manager)

    assert "plan_request_review" in manager.run_completion_error()
    failed = manager.fail_run(reason=manager.run_completion_error())

    assert failed.status == "failed"
    assert failed.plan_active is False
    assert manager.drain_runtime_events()[-1]["type"] == "PLAN_FAILED"


def test_failed_revision_returns_to_existing_review() -> None:
    session_id = f"session-{uuid.uuid4().hex}"
    manager = _manager(persist=True, session_id=session_id)
    _draft(manager)
    plan = manager.request_review()
    revising = PlanModeManager(
        session_id=session_id,
        run_id=f"run-{uuid.uuid4().hex}",
        agent_id="agent-plan",
        agent_name="Agent",
        persist=True,
        plan_id=plan.plan_id,
        action="revise",
        feedback="修改失败也要保留上一版",
    )

    restored = revising.fail_run(reason="model unavailable")
    events = revising.drain_runtime_events()

    assert restored.status == "review_waiting"
    assert restored.revision == 1
    assert restored.plan_active is False
    assert events[-1]["type"] == "PLAN_REVIEW_REQUESTED"
    assert events[-1]["payload"]["attemptFailed"] is True


def test_review_action_uses_atomic_status_transition() -> None:
    session_id = f"session-{uuid.uuid4().hex}"
    manager = _manager(persist=True, session_id=session_id)
    _draft(manager)
    plan = manager.request_review()
    first = PlanModeManager(
        session_id=session_id,
        run_id=f"run-{uuid.uuid4().hex}",
        agent_id="agent-plan",
        agent_name="Agent",
        persist=True,
        plan_id=plan.plan_id,
    )
    stale = PlanModeManager(
        session_id=session_id,
        run_id=f"run-{uuid.uuid4().hex}",
        agent_id="agent-plan",
        agent_name="Agent",
        persist=True,
        plan_id=plan.plan_id,
    )

    first._prepare_action("approve")
    try:
        stale._prepare_action("approve")
    except ValueError as exc:
        assert "already handled" in str(exc)
    else:
        raise AssertionError("A stale review request must not start duplicate execution")
