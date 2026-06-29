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
    """退出计划模式并等待用户审批。"""

    name: str = "plan_exit"
    description: str = "Exit PLAN mode and ask the user to approve the written plan."
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    async def __call__(self) -> ToolChunk:
        context = self.manager.exit_for_approval()
        return self._success({
            "planId": context.plan_id,
            "planFile": context.current_plan_file,
            "waitingApproval": context.waiting_approval,
            "markdown": context.markdown,
        })


def build_plan_mode_tools(manager: PlanModeManager) -> list[ToolBase]:
    """构造 Plan Mode 控制工具。"""

    return [
        PlanEnterTool(manager),
        PlanWriteTool(manager),
        PlanExitTool(manager),
    ]
