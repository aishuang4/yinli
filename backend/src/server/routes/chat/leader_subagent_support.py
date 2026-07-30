"""Leader 流定位并识别 attached Subagent 外部工具。"""

from src.subagents.manager import SubagentDelegationManager


SUBAGENT_TASK_TOOL_NAMES = {
    "DelegateTask",
    "SubagentTaskList",
    "SubagentTaskWait",
    "SubagentResultRead",
    "SubagentTaskCancel",
}


def is_subagent_task_external_event(event_data: dict) -> bool:
    """判断 AgentScope 外部执行事件是否只包含 attached Subagent 工具。"""

    tool_calls = list(event_data.get("tool_calls") or [])
    return bool(tool_calls) and all(
        str(tool_call.get("name") or "") in SUBAGENT_TASK_TOOL_NAMES
        for tool_call in tool_calls
    )


async def find_subagent_manager(
    sources: list[object],
    *,
    agent: object | None = None,
) -> object | None:
    """从 source 或当前 Agent Toolkit 找到 Subagent manager。"""

    for source in sources:
        if isinstance(source, SubagentDelegationManager) or callable(
            getattr(source, "execute_external_tool_call", None),
        ):
            return source
    toolkit = getattr(agent, "toolkit", None)
    get_tool = getattr(toolkit, "get_tool", None)
    if callable(get_tool):
        delegate_tool = await get_tool("DelegateTask")
        manager = getattr(delegate_tool, "_manager", None)
        if isinstance(manager, SubagentDelegationManager) or callable(
            getattr(manager, "execute_external_tool_call", None),
        ):
            return manager
    return None
