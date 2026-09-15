"""AskUser external tool for AgentScope HITL resume."""

from typing import Any

from agentscope.message import TextBlock, ToolResultState
from agentscope.permission import PermissionBehavior, PermissionContext, PermissionDecision
from agentscope.tool import ToolBase, ToolChunk


class AskUser(ToolBase):
    """Pause the Agent run and ask the user for structured input."""

    name: str = "AskUser"
    description: str = (
        "Ask the human user for missing information and pause the current ReAct loop; it resumes after "
        "the user answers. Use only when the task cannot continue reliably without the input. At most 3 "
        "focused questions; choice / multi_choice questions need 2-5 concise options; set required=true "
        "only for information that truly blocks execution. Never ask blocking questions as plain text, and "
        "do not use this for normal final answers."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Short title shown to the user.",
            },
            "description": {
                "type": "string",
                "description": "Brief explanation of why this information is required.",
            },
            "questions": {
                "type": "array",
                "description": "One to three focused questions.",
                "minItems": 1,
                "maxItems": 3,
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "string",
                            "description": "Stable snake_case question id.",
                        },
                        "type": {
                            "type": "string",
                            "enum": ["choice", "multi_choice", "text"],
                            "description": "Question input type.",
                        },
                        "label": {
                            "type": "string",
                            "description": "User-facing question label.",
                        },
                        "options": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Required for choice and multi_choice questions.",
                        },
                        "placeholder": {
                            "type": "string",
                            "description": "Placeholder for text questions.",
                        },
                        "required": {
                            "type": "boolean",
                            "description": (
                                "Whether this specific answer is required to continue. "
                                "Use false for optional supporting details."
                            ),
                        },
                    },
                    "required": ["id", "type", "label", "required"],
                },
            },
        },
        "required": ["title", "questions"],
    }
    is_mcp: bool = False
    is_read_only: bool = True
    is_concurrency_safe: bool = False
    is_external_tool: bool = True
    is_state_injected: bool = False

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="AskUser is an external HITL tool and should pause for user input.",
            decision_reason="Human input requested",
        )

    async def __call__(self, **kwargs: Any) -> ToolChunk:
        return ToolChunk(
            content=[TextBlock(text="AskUser waits for ExternalExecutionResultEvent and is not executed locally.")],
            state=ToolResultState.SUCCESS,
            is_last=True,
        )
