"""DelegateTask 工具，供主 Agent 前台委托 Subagent。"""

from typing import Any

from agentscope.message import TextBlock, ToolCallBlock, ToolCallState, ToolResultState
from agentscope.permission import PermissionBehavior, PermissionContext, PermissionDecision
from agentscope.state import AgentState
from agentscope.tool import ToolBase, ToolChunk

from src.subagents.manager import SubagentDelegationManager


class DelegateTask(ToolBase):
    """把自包含任务委托给一个文件定义的 Subagent。"""

    name: str = "DelegateTask"
    description: str = (
        "Delegate a self-contained side task to a named subagent with an independent context. "
        "Use this when exploration or analysis would pollute the main conversation, and only a concise "
        "summary is needed before continuing. In V1 this call is blocking: wait for the subagent result "
        "before continuing. Call at most one DelegateTask per model reply."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "subagentName": {
                "type": "string",
                "description": "Name of the subagent definition to run.",
            },
            "task": {
                "type": "string",
                "description": "Self-contained task for the subagent.",
            },
            "expectedOutput": {
                "type": "string",
                "description": "Concise description of the desired result format.",
            },
            "context": {
                "type": "string",
                "description": "Optional concise context from the main agent.",
            },
            "constraints": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional constraints the subagent must follow.",
            },
        },
        "required": ["subagentName", "task"],
    }
    is_mcp: bool = False
    is_read_only: bool = True
    is_concurrency_safe: bool = False
    is_external_tool: bool = True
    is_state_injected: bool = True

    def __init__(self, manager: SubagentDelegationManager) -> None:
        super().__init__()
        self._manager = manager

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="DelegateTask only starts a Subagent within the current parent guardrails.",
            decision_reason="Subagent delegation",
        )

    async def __call__(
        self,
        subagentName: str,
        task: str,
        expectedOutput: str = "",
        context: str = "",
        constraints: list[str] | None = None,
        _agent_state: AgentState | None = None,
        **kwargs: Any,
    ) -> ToolChunk:
        try:
            result = await self._manager.delegate(
                subagent_name=subagentName,
                task=task,
                expected_output=expectedOutput,
                context=context,
                constraints=constraints or [],
                tool_call_id=_extract_current_tool_call_id(_agent_state, self.name),
            )
            return ToolChunk(
                content=[TextBlock(text=result)],
                state=ToolResultState.SUCCESS,
                is_last=True,
            )
        except Exception as exc:
            return ToolChunk(
                content=[TextBlock(text=f"Subagent delegation failed: {exc}")],
                state=ToolResultState.ERROR,
                is_last=True,
            )


def _extract_current_tool_call_id(state: AgentState | None, tool_name: str) -> str:
    """从 AgentState 中提取当前工具调用 ID，用于关联 Subagent 审计事件。"""

    if state is None:
        return ""

    fallback_id = ""
    unfinished_states = {
        str(ToolCallState.PENDING),
        str(ToolCallState.ASKING),
        str(ToolCallState.ALLOWED),
        str(ToolCallState.SUBMITTED),
    }
    for message in reversed(state.context):
        content = message.content
        if not isinstance(content, list):
            continue
        for block in reversed(content):
            if not isinstance(block, ToolCallBlock):
                continue
            if block.name != tool_name:
                continue
            fallback_id = fallback_id or str(block.id or "")
            if str(block.state) in unfinished_states:
                return str(block.id or "")
    return fallback_id
