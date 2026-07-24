"""Plan Mode AgentScope Middleware。"""

from typing import Any, AsyncGenerator

from agentscope.message import TextBlock, ToolResultState
from agentscope.middleware import MiddlewareBase
from agentscope.tool import ToolResponse

from src.server.routes.chat.plan_mode.manager import PlanModeManager
from src.server.routes.chat.plan_mode.policy import is_plan_mode_tool_allowed


DENY_MESSAGE = (
    "Blocked: you are in PLAN mode (read-only). You may investigate and run "
    "read-only tools, record your plan with plan_write, and call plan_request_review "
    "when ready to return the plan. Do not modify files or run mutating "
    "commands while PLAN mode is active."
)

PLAN_BANNER_TEMPLATE = """

<system-reminder>
PLAN MODE is active (read-only). Plan file: {plan_file}
If no Plan ID exists yet, call plan_enter before any other Plan control tool.
Investigate the problem and draft a concrete plan, but do NOT modify files,
run mutating commands, or otherwise change state. Record the plan with
plan_write. After writing the plan, use plan_set_steps to create a structured
task list so the user can review the intended execution steps.
When the plan is complete, call plan_request_review. This pauses work for an
explicit user review. Never implement the plan before the user approves it.
If you lack required information, ask one specific clarifying question
instead of inventing details.
When the user or context gives an exact relative path, call Read on that path
directly. Avoid broad Glob patterns such as **/* unless you truly need project
discovery; prefer scoped Grep/Glob inside the smallest known directory.
</system-reminder>
"""

EXECUTION_BANNER_TEMPLATE = """

<system-reminder>
An approved PLAN is being executed. Plan ID: {plan_id}; approved revision:
{approved_revision}. Follow the structured steps in order. Before starting a
step call task_output with status=running. After it finishes call task_output
again with completed, failed, or skipped and include concise evidence/result.
If new evidence invalidates the approved plan, call plan_request_replan and
produce a complete new revision for review. When every step has a terminal
status, call plan_complete before the final answer.
</system-reminder>
"""


class PlanModeMiddleware(MiddlewareBase):
    """通过 AgentScope hooks 实现 Plan Mode 只读规划。"""

    def __init__(self, manager: PlanModeManager) -> None:
        self.manager = manager

    async def on_system_prompt(self, agent: object, current_prompt: str) -> str:
        """按当前 Plan Mode 状态注入系统提示。"""

        base = current_prompt or ""
        if self.manager.context.plan_active:
            base += PLAN_BANNER_TEMPLATE.format(
                plan_file=self.manager.plan_file_path(),
            )
        elif self.manager.is_execution_active():
            base += EXECUTION_BANNER_TEMPLATE.format(
                plan_id=self.manager.context.plan_id,
                approved_revision=self.manager.context.approved_revision,
            )
        return base + self.manager.prompt_context()

    async def on_acting(
        self,
        agent: object,
        input_kwargs: dict[str, Any],
        next_handler: object,
    ) -> AsyncGenerator[object, None]:
        """Plan Mode 下拒绝变更型工具调用。"""

        tool_call = input_kwargs.get("tool_call")
        if not self.manager.is_plan_active() or tool_call is None:
            async for item in next_handler(**input_kwargs):
                yield item
            return

        tool_name = str(getattr(tool_call, "name", "") or "")
        raw_input = getattr(tool_call, "input", None)
        if is_plan_mode_tool_allowed(tool_name, raw_input):
            async for item in next_handler(**input_kwargs):
                yield item
            return

        tool_call_id = str(getattr(tool_call, "id", "") or "")
        self.manager.record_tool_denied(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            message=DENY_MESSAGE,
        )
        yield ToolResponse(
            id=tool_call_id,
            content=[TextBlock(text=DENY_MESSAGE)],
            state=ToolResultState.DENIED,
        )
