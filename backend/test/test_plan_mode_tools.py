import asyncio

from agentscope.message import ToolResultState

from src.server.routes.chat.plan_mode.manager import PlanModeManager
from src.server.routes.chat.plan_mode.tools import PlanEnterTool, PlanExitTool, PlanWriteTool


def test_manager_enters_updates_and_exits_plan_mode() -> None:
    manager = PlanModeManager(
        session_id="session-1",
        run_id="run-1",
        agent_id="agent-1",
        agent_name="Agent",
        persist=False,
    )

    entered = manager.enter(goal="实现 Plan Mode", title="Plan Mode")
    assert entered.plan_active is True
    assert entered.plan_id
    assert entered.current_plan_file.endswith(".md")

    updated = manager.write(markdown="# Plan\n\n- [ ] Step 1", source="test")
    assert updated.markdown == "# Plan\n\n- [ ] Step 1"
    assert manager.context.current_plan_file == entered.current_plan_file

    waiting = manager.exit_for_approval()
    assert waiting.plan_active is False
    assert waiting.waiting_approval is True
    assert waiting.approved is False


def test_manager_enter_is_idempotent_for_same_run() -> None:
    """模型重复调用 plan_enter 时复用当前计划，不重复创建记录。"""

    manager = PlanModeManager(
        session_id="session-1",
        run_id="run-1",
        agent_id="agent-1",
        agent_name="Agent",
        persist=False,
    )

    first = manager.enter(goal="制定计划", title="计划")
    second = manager.enter(goal="制定计划", title="计划")

    assert second.plan_id == first.plan_id
    assert second.current_plan_file == first.current_plan_file
    assert second.markdown == first.markdown


def test_manager_drains_plan_runtime_events() -> None:
    manager = PlanModeManager(
        session_id="session-3",
        run_id="run-3",
        agent_id="agent-3",
        agent_name="Agent",
        persist=False,
    )
    manager.enter(goal="制定计划")
    events = manager.drain_runtime_events()

    assert [event["type"] for event in events] == ["PLAN_MODE_ENTERED"]
    assert manager.drain_runtime_events() == []


def test_plan_tools_update_manager_context() -> None:
    manager = PlanModeManager(
        session_id="session-2",
        run_id="run-2",
        agent_id="agent-2",
        agent_name="Agent",
        persist=False,
    )

    enter = asyncio.run(PlanEnterTool(manager)(goal="调查并制定计划", title="调查计划"))
    assert enter.state == ToolResultState.SUCCESS
    assert manager.context.plan_active is True

    write = asyncio.run(PlanWriteTool(manager)(markdown="# 调查计划\n\n- [ ] 读取代码"))
    assert write.state == ToolResultState.SUCCESS
    assert "读取代码" in manager.context.markdown

    exit_result = asyncio.run(PlanExitTool(manager)())
    assert exit_result.state == ToolResultState.SUCCESS
    assert manager.context.waiting_approval is True
