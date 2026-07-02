import asyncio
import json

from agentscope.message import ToolResultState

from src.server.routes.chat.plan_mode.manager import PlanModeManager
from src.server.routes.chat.plan_mode.tools import (
    PlanEnterTool,
    PlanExitTool,
    PlanWriteTool,
    TaskListTool,
    TaskOutputTool,
    TodoWriteTool,
)


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

    finalized = manager.finalize()
    assert finalized.plan_active is False
    assert finalized.markdown == "# Plan\n\n- [ ] Step 1"


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
    exit_payload = json.loads(exit_result.content[0].text)
    assert exit_payload == {
        "planId": manager.context.plan_id,
        "planFile": manager.context.current_plan_file,
        "markdown": "# 调查计划\n\n- [ ] 读取代码",
    }


def test_manager_writes_structured_plan_tasks() -> None:
    manager = PlanModeManager(
        session_id="session-tasks",
        run_id="run-tasks",
        agent_id="agent-tasks",
        agent_name="Agent",
        persist=False,
    )
    manager.enter(goal="补齐 Plan task 工具", title="Plan task")
    manager.drain_runtime_events()

    context = manager.write_tasks(items=[{
        "title": "核对数据结构",
        "description": "确认 plans 和 plan_steps 字段。",
    }])

    assert context.steps[0]["title"] == "核对数据结构"
    assert context.steps[0]["status"] == "pending"
    assert context.steps[0]["stepIndex"] == 0
    events = manager.drain_runtime_events()
    assert events[-1]["type"] == "PLAN_TASKS_UPDATED"
    assert events[-1]["payload"]["steps"][0]["title"] == "核对数据结构"


def test_manager_records_task_output() -> None:
    manager = PlanModeManager(
        session_id="session-output",
        run_id="run-output",
        agent_id="agent-output",
        agent_name="Agent",
        persist=False,
    )
    manager.enter(goal="补齐 Plan task 工具", title="Plan task")
    manager.write_tasks(items=[{"title": "补工具"}])
    manager.drain_runtime_events()
    step = manager.list_tasks()[0]

    context = manager.record_task_output(
        step_id=step["id"],
        status="completed",
        evidence="单元测试通过",
        result="工具返回结构化步骤。",
    )

    assert context.steps[0]["status"] == "completed"
    assert context.steps[0]["evidence"] == "单元测试通过"
    assert context.steps[0]["result"] == "工具返回结构化步骤。"
    assert context.steps[0]["completedAt"]
    events = manager.drain_runtime_events()
    assert events[-1]["type"] == "PLAN_TASK_OUTPUT"
    assert events[-1]["status"] == "completed"
    assert events[-1]["payload"]["step"]["result"] == "工具返回结构化步骤。"


def test_e2e_plan_enter_todo_write_exit_finalizes_plan() -> None:
    """端到端集成测试：plan_enter → todo_write → plan_exit。"""
    manager = PlanModeManager(
        session_id="session-e2e",
        run_id="run-e2e",
        agent_id="agent-e2e",
        agent_name="Agent",
        persist=False,
    )

    # 1. plan_enter
    entered = manager.enter(goal="端到端测试", title="E2E Test")
    assert entered.plan_active is True
    assert entered.plan_id
    manager.drain_runtime_events()

    # 2. todo_write — 写入结构化任务列表
    context = manager.write_tasks(items=[
        {"title": "步骤 A", "description": "实现功能 A"},
        {"title": "步骤 B", "description": "实现功能 B"},
    ])
    assert len(context.steps) == 2
    assert context.steps[0]["title"] == "步骤 A"
    assert context.steps[0]["status"] == "pending"
    assert context.steps[1]["title"] == "步骤 B"
    tasks_events = manager.drain_runtime_events()
    assert tasks_events[-1]["type"] == "PLAN_TASKS_UPDATED"
    assert len(tasks_events[-1]["payload"]["steps"]) == 2

    # 3. plan_exit — 将计划作为本轮最终答复
    finalized = manager.finalize()
    assert finalized.plan_active is False
    assert finalized.markdown
    exit_events = manager.drain_runtime_events()
    assert exit_events[-1]["type"] == "PLAN_FINALIZED"
    assert exit_events[-1]["status"] == "completed"
    assert exit_events[-1]["payload"]["markdown"] == finalized.markdown

    # 4. task_list — 仍可读取当前结构化步骤
    steps = manager.list_tasks()
    assert len(steps) == 2
    assert steps[0]["status"] == "pending"

    # 5. task_output — 更新步骤状态
    step_a = steps[0]
    context = manager.record_task_output(
        step_id=step_a["id"],
        status="completed",
        evidence="功能 A 已实现并通过测试",
        result="步骤 A 完成",
    )
    assert context.steps[0]["status"] == "completed"
    assert context.steps[0]["evidence"] == "功能 A 已实现并通过测试"
    assert context.steps[0]["result"] == "步骤 A 完成"
    assert context.steps[0]["completedAt"]
    output_events = manager.drain_runtime_events()
    assert output_events[-1]["type"] == "PLAN_TASK_OUTPUT"
    assert output_events[-1]["status"] == "completed"
    assert output_events[-1]["payload"]["step"]["result"] == "步骤 A 完成"

    # 6. 验证 task_list 能读取到更新后的步骤
    updated_steps = manager.list_tasks()
    assert updated_steps[0]["status"] == "completed"
    assert updated_steps[1]["status"] == "pending"


def test_task_tools_return_structured_payloads() -> None:
    manager = PlanModeManager(
        session_id="session-tool-tasks",
        run_id="run-tool-tasks",
        agent_id="agent-tool-tasks",
        agent_name="Agent",
        persist=False,
    )
    asyncio.run(PlanEnterTool(manager)(goal="补齐任务进度", title="任务进度"))

    todo_result = asyncio.run(TodoWriteTool(manager)(items=[
        {"title": "补 manager", "description": "维护结构化步骤。"},
        {"title": "补前端", "description": "展示任务进度。"},
    ]))
    assert todo_result.state == ToolResultState.SUCCESS
    todo_payload = json.loads(todo_result.content[0].text)
    assert len(todo_payload["steps"]) == 2

    list_result = asyncio.run(TaskListTool(manager)())
    list_payload = json.loads(list_result.content[0].text)
    assert list_payload["steps"][1]["title"] == "补前端"

    output_result = asyncio.run(TaskOutputTool(manager)(
        stepIndex=1,
        status="running",
        evidence="已定位 ProcessDetailsBlock。",
        result="开始实现 UI。",
    ))
    output_payload = json.loads(output_result.content[0].text)
    assert output_payload["step"]["status"] == "running"
    assert output_payload["step"]["result"] == "开始实现 UI。"
