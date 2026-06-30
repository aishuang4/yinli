"""Plan Mode AgentScope Middleware。"""

from typing import Any, AsyncGenerator

from agentscope.message import TextBlock, ToolResultState
from agentscope.middleware import MiddlewareBase
from agentscope.tool import ToolResponse

from src.server.routes.chat.plan_mode.manager import PlanModeManager
from src.server.routes.chat.plan_mode.policy import is_plan_mode_tool_allowed


DENY_MESSAGE = (
    "Blocked: you are in PLAN mode (read-only). You may investigate and run "
    "read-only tools, record your plan with plan_write, and call plan_exit "
    "when ready to request approval. Do not modify files or run mutating "
    "commands until the plan is approved."
)

PLAN_BANNER_TEMPLATE = """

<system-reminder>
PLAN MODE is active (read-only). Plan file: {plan_file}
Investigate the problem and draft a concrete plan, but do NOT modify files,
run mutating commands, or otherwise change state. Record the plan with
plan_write. After writing the plan, use todo_write to create a structured
task list so the user and BUILD mode agent can track progress step by step.
When the plan is complete, call plan_exit to ask the user for approval.
If you lack required information, ask one specific clarifying question
instead of inventing details.
When the user or context gives an exact relative path, call Read on that path
directly. Avoid broad Glob patterns such as **/* unless you truly need project
discovery; prefer scoped Grep/Glob inside the smallest known directory.
</system-reminder>
"""

BUILD_MODE_HINT_TEMPLATE = """

<system-reminder>
BUILD mode is active. The approved plan exists at {plan_file}. Read or use it
as the execution guide, then execute the task step by step until complete.
Do not stop after merely producing the plan.

You have three structured task-progress tools available:
- todo_write — Create or replace the structured task list for the active plan.
  Use this in the PLAN phase to write steps. In BUILD mode the steps are
  typically already written; use it only if you need to adjust the task list.
- task_list — List the structured tasks for the current plan. Call this before
  executing to see what tasks are defined and their current status.
- task_output — Update one structured plan task with status, evidence, and
  result. Call this after each meaningful step so the user can track progress
  in the UI. Do not skip task_output calls; each completed step must be reported.

When a plan step names an exact relative path, call Read/Edit on that path
directly instead of searching the entire project with broad Glob patterns.
After all plan steps are complete and before writing the user-facing result,
call FinalAnswerStart exactly once, then continue with the final answer as
normal assistant text.

Approved plan:
{markdown}
</system-reminder>
"""


class PlanModeMiddleware(MiddlewareBase):
    """通过 AgentScope hooks 实现 Plan/Build 动态模式。"""

    def __init__(self, manager: PlanModeManager) -> None:
        self.manager = manager

    async def on_system_prompt(self, agent: object, current_prompt: str) -> str:
        """按当前 Plan Mode 状态注入系统提示。"""

        base = current_prompt or ""
        if self.manager.context.plan_active:
            return base + PLAN_BANNER_TEMPLATE.format(
                plan_file=self.manager.plan_file_path(),
            )
        if self.manager.context.current_plan_file and self.manager.context.approved:
            return base + BUILD_MODE_HINT_TEMPLATE.format(
                plan_file=self.manager.context.current_plan_file,
                markdown=self.manager.context.markdown,
            )
        return base

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
