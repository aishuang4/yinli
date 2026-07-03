"""Final answer phase marker tool."""

from typing import Any

from agentscope.message import TextBlock, ToolResultState
from agentscope.permission import PermissionBehavior, PermissionContext, PermissionDecision
from agentscope.tool import ToolBase, ToolChunk


class FinalAnswerStart(ToolBase):
    """Mark that all following assistant text is the final answer."""

    name: str = "FinalAnswerStart"
    description: str = (
        "Call this exactly once immediately before the final user-facing answer. A successful run "
        "must not end with a final answer unless this tool has been called. Do not put the final "
        "answer inside this tool; call it, then continue with the final answer as normal assistant text."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "rationale": {
                "type": "string",
                "description": "Brief reason why the agent is ready to provide the final answer.",
            },
        },
        "required": [],
    }
    is_mcp: bool = False
    is_read_only: bool = True
    is_concurrency_safe: bool = True
    is_external_tool: bool = False
    is_state_injected: bool = False

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="FinalAnswerStart only switches the response phase.",
            decision_reason="Final answer phase marker",
        )

    async def __call__(self, **kwargs: Any) -> ToolChunk:
        return ToolChunk(
            content=[TextBlock(text="Final answer phase started.")],
            state=ToolResultState.SUCCESS,
            is_last=True,
        )
