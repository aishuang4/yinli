"""主 Agent 可见的 Subagent attached task 工具。"""

from __future__ import annotations

from typing import Any

from agentscope.message import TextBlock, ToolResultState
from agentscope.permission import PermissionBehavior, PermissionContext, PermissionDecision
from agentscope.state import AgentState
from agentscope.tool import ToolBase, ToolChunk

from src.subagents.manager import SubagentDelegationManager, subagent_error_observation


class _SubagentExternalTool(ToolBase):
    """共享 Subagent 外部工具的权限和执行属性。"""

    is_mcp: bool = False
    is_read_only: bool = True
    is_concurrency_safe: bool = True
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
            message="Subagent tools operate within the current parent guardrails.",
            decision_reason="Subagent attached task orchestration",
        )

    @staticmethod
    def _success(text: str) -> ToolChunk:
        return ToolChunk(content=[TextBlock(text=text)], state=ToolResultState.SUCCESS, is_last=True)

    @staticmethod
    def _failure(exc: Exception) -> ToolChunk:
        return ToolChunk(
            content=[TextBlock(text=subagent_error_observation(exc))],
            state=ToolResultState.ERROR,
            is_last=True,
        )


class DelegateTask(_SubagentExternalTool):
    """按 YAML/Markdown definition fork 一个 attached Subagent task。"""

    name: str = "DelegateTask"
    description: str = (
        "Fork a self-contained attached task to a named subagent with an independent context. "
        "This returns an invocationId immediately. Continue useful parent work, then call "
        "SubagentTaskWait and SubagentResultRead for every child before finalizing."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "subagentName": {"type": "string", "description": "Subagent definition name."},
            "task": {"type": "string", "description": "Self-contained child task."},
            "expectedOutput": {"type": "string", "description": "Desired result format."},
            "context": {"type": "string", "description": "Concise parent context."},
            "constraints": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Constraints the child must follow.",
            },
        },
        "required": ["subagentName", "task"],
    }

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
            return self._success(await self._manager.delegate(
                subagent_name=subagentName,
                task=task,
                expected_output=expectedOutput,
                context=context,
                constraints=constraints or [],
                tool_call_id=_extract_current_tool_call_id(_agent_state, self.name),
            ))
        except Exception as exc:
            return self._failure(exc)


class SubagentTaskList(_SubagentExternalTool):
    """列出当前父 Run 创建的 attached tasks。"""

    name: str = "SubagentTaskList"
    description: str = "List attached subagent task statuses for the current parent run without reading results."
    input_schema: dict[str, Any] = {"type": "object", "properties": {}}

    async def __call__(self, **kwargs: Any) -> ToolChunk:
        try:
            return self._success(self._manager.list_tasks())
        except Exception as exc:
            return self._failure(exc)


class SubagentTaskWait(_SubagentExternalTool):
    """等待指定 child tasks 结束或进入需要处理的 waiting 状态。"""

    name: str = "SubagentTaskWait"
    description: str = (
        "Wait until any or all attached subagent tasks leave queued/running. "
        "This returns status and result references only; read every terminal result afterward."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "invocationIds": {"type": "array", "items": {"type": "string"}, "minItems": 1},
            "waitMode": {"type": "string", "enum": ["any", "all"], "default": "all"},
            "timeoutSeconds": {"type": "integer", "minimum": 1, "maximum": 1800, "default": 300},
        },
        "required": ["invocationIds"],
    }

    async def __call__(
        self,
        invocationIds: list[str],
        waitMode: str = "all",
        timeoutSeconds: int = 300,
        **kwargs: Any,
    ) -> ToolChunk:
        try:
            return self._success(await self._manager.wait_tasks(
                invocationIds,
                wait_mode=waitMode,
                timeout_seconds=timeoutSeconds,
            ))
        except Exception as exc:
            return self._failure(exc)


class SubagentResultRead(_SubagentExternalTool):
    """读取一个终态 child 的 result.md。"""

    name: str = "SubagentResultRead"
    description: str = (
        "Read the final result.md for one terminal attached subagent task. "
        "You must use this for completed, failed, cancelled, or interrupted tasks before finalizing."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"invocationId": {"type": "string"}},
        "required": ["invocationId"],
    }

    async def __call__(
        self,
        invocationId: str,
        _agent_state: AgentState | None = None,
        **kwargs: Any,
    ) -> ToolChunk:
        try:
            return self._success(self._manager.read_result(
                invocationId,
                tool_call_id=_extract_current_tool_call_id(_agent_state, self.name),
            ))
        except Exception as exc:
            return self._failure(exc)


class SubagentTaskCancel(_SubagentExternalTool):
    """取消一个当前父 Run 的 attached child。"""

    name: str = "SubagentTaskCancel"
    description: str = "Cancel an attached subagent task. The cancellation result must still be read and consumed."
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"invocationId": {"type": "string"}},
        "required": ["invocationId"],
    }

    async def __call__(self, invocationId: str, **kwargs: Any) -> ToolChunk:
        try:
            return self._success(await self._manager.cancel_task(invocationId))
        except Exception as exc:
            return self._failure(exc)


def build_subagent_tools(manager: SubagentDelegationManager) -> list[ToolBase]:
    """构造主 Agent完整的 attached task 工具组。"""

    return [
        DelegateTask(manager),
        SubagentTaskList(manager),
        SubagentTaskWait(manager),
        SubagentResultRead(manager),
        SubagentTaskCancel(manager),
    ]


def _extract_current_tool_call_id(state: AgentState | None, tool_name: str) -> str:
    """从 AgentState 中提取当前工具调用 ID。"""

    if state is None:
        return ""
    for message in reversed(state.context):
        content = message.content
        if not isinstance(content, list):
            continue
        for block in reversed(content):
            if getattr(block, "name", "") == tool_name and getattr(block, "id", ""):
                return str(block.id)
    return ""
