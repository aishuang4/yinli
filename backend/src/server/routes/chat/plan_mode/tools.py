"""Plan Mode 控制工具。"""

import json
from typing import Any

from agentscope.message import TextBlock, ToolResultState
from agentscope.permission import PermissionBehavior, PermissionContext, PermissionDecision
from agentscope.tool import ToolBase, ToolChunk

from src.server.routes.chat.plan_mode.manager import PlanModeManager


class _PlanToolBase(ToolBase):
    """Plan Mode 工具基类，统一权限为允许。"""

    is_mcp: bool = False
    is_read_only: bool = True
    is_concurrency_safe: bool = False
    is_external_tool: bool = False
    is_state_injected: bool = False

    def __init__(self, manager: PlanModeManager) -> None:
        self.manager = manager

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message=f"{self.name} is controlled by Plan Mode middleware.",
            decision_reason="Plan Mode control tool",
        )

    def _success(self, payload: dict[str, Any]) -> ToolChunk:
        return ToolChunk(
            content=[TextBlock(text=json.dumps(payload, ensure_ascii=False, indent=2))],
            state=ToolResultState.SUCCESS,
            is_last=True,
        )


class PlanEnterTool(_PlanToolBase):
    """进入只读计划模式。"""

    name: str = "plan_enter"
    description: str = "Enter PLAN mode before investigation and planning."
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "goal": {"type": "string", "description": "User goal for the plan."},
            "title": {"type": "string", "description": "Short plan title."},
        },
        "required": ["goal"],
    }

    async def __call__(self, goal: str, title: str = "Runtime Plan") -> ToolChunk:
        context = self.manager.enter(goal=goal, title=title)
        return self._success({
            "planId": context.plan_id,
            "planFile": context.current_plan_file,
            "planActive": context.plan_active,
        })


class PlanWriteTool(_PlanToolBase):
    """写入当前计划 markdown。"""

    name: str = "plan_write"
    description: str = "Write or replace the current PLAN mode markdown plan."
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "markdown": {"type": "string", "description": "Complete plan markdown."},
        },
        "required": ["markdown"],
    }

    async def __call__(self, markdown: str) -> ToolChunk:
        context = self.manager.write(markdown=markdown, source="tool")
        return self._success({
            "planId": context.plan_id,
            "planFile": context.current_plan_file,
            "markdown": context.markdown,
        })


class PlanExitTool(_PlanToolBase):
    """退出计划模式并将计划作为最终答复。"""

    name: str = "plan_exit"
    description: str = "Exit PLAN mode and return the written plan as the final answer."
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    async def __call__(self) -> ToolChunk:
        context = self.manager.finalize()
        return self._success({
            "planId": context.plan_id,
            "planFile": context.current_plan_file,
            "markdown": context.markdown,
        })


class TodoWriteTool(_PlanToolBase):
    """写入结构化任务列表。"""

    name: str = "todo_write"
    description: str = "Create or replace the structured task list for the active plan."
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "description": "Structured plan tasks in execution order.",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Short task title."},
                        "description": {"type": "string", "description": "Task details."},
                        "status": {
                            "type": "string",
                            "description": "Task status.",
                            "enum": ["pending", "running", "completed", "failed", "skipped"],
                        },
                        "assignedAgentId": {"type": "string", "description": "Optional assigned agent id."},
                        "evidence": {"type": "string", "description": "Evidence collected for this task."},
                        "result": {"type": "string", "description": "Task result summary."},
                    },
                    "required": ["title"],
                },
            },
        },
        "required": ["items"],
    }

    async def __call__(self, items: list[dict[str, Any]]) -> ToolChunk:
        context = self.manager.write_tasks(items=items)
        return self._success({
            "planId": context.plan_id,
            "planFile": context.current_plan_file,
            "steps": context.steps,
        })


class TaskListTool(_PlanToolBase):
    """读取当前结构化任务列表。"""

    name: str = "task_list"
    description: str = "List structured tasks for the current plan."
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    async def __call__(self) -> ToolChunk:
        steps = self.manager.list_tasks()
        return self._success({
            "planId": self.manager.context.plan_id,
            "planFile": self.manager.context.current_plan_file,
            "steps": steps,
        })


class TaskOutputTool(_PlanToolBase):
    """更新某个结构化任务的状态与输出。"""

    name: str = "task_output"
    description: str = "Update one structured plan task with status, evidence, and result."
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "stepId": {"type": "string", "description": "Structured task id."},
            "stepIndex": {"type": "integer", "description": "Zero-based task index."},
            "status": {
                "type": "string",
                "description": "Task status.",
                "enum": ["pending", "running", "completed", "failed", "skipped"],
            },
            "evidence": {"type": "string", "description": "Evidence collected for this task."},
            "result": {"type": "string", "description": "Task result summary."},
        },
        "required": ["status"],
    }

    async def __call__(
        self,
        status: str,
        stepId: str | None = None,
        stepIndex: int | None = None,
        evidence: str = "",
        result: str = "",
    ) -> ToolChunk:
        context = self.manager.record_task_output(
            step_id=stepId,
            step_index=stepIndex,
            status=status,
            evidence=evidence,
            result=result,
        )
        step = next((
            item for item in context.steps
            if (stepId and str(item.get("id") or "") == stepId)
            or (stepIndex is not None and int(item.get("stepIndex") or 0) == stepIndex)
        ), None)
        return self._success({
            "planId": context.plan_id,
            "planFile": context.current_plan_file,
            "step": step or {},
            "steps": context.steps,
        })


def build_plan_mode_tools(manager: PlanModeManager) -> list[ToolBase]:
    """构造 Plan Mode 控制工具。"""

    return [
        PlanEnterTool(manager),
        PlanWriteTool(manager),
        TodoWriteTool(manager),
        TaskListTool(manager),
        TaskOutputTool(manager),
        PlanExitTool(manager),
    ]
