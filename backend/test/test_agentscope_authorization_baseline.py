"""固定 AgentScope 2.0.5 的真实工具许可与恢复合同。"""

import agentscope
from agentscope.event import RequireExternalExecutionEvent, RequireUserConfirmEvent, ToolResultEndEvent
from agentscope.message import ToolCallState
from agentscope.permission import PermissionBehavior, PermissionEngine

from test.authorization_fakes import ActingRecorder, MemoryTool, collect, make_agent, make_call, run_async


@run_async
async def test_engine_precedes_acting_and_native_result_pair():
    assert agentscope.__version__ == "2.0.5"
    log, call = [], make_call()
    agent = make_agent(MemoryTool(log), [call], middlewares=[ActingRecorder(log)])
    assert type(agent._engine) is PermissionEngine
    events = await collect(agent._execute_tool_call(call))
    assert [item[0] for item in log] == ["engine", "acting", "call"]
    assert sum(isinstance(item, ToolResultEndEvent) for item in events) == 1


@run_async
async def test_external_never_enters_acting():
    log, call = [], make_call()
    agent = make_agent(MemoryTool(log, external=True), [call], middlewares=[ActingRecorder(log)])
    events = await collect(agent._execute_tool_call(call))
    assert [item[0] for item in log] == ["engine"]
    assert isinstance(events[-1], RequireExternalExecutionEvent)
    assert call.state == ToolCallState.SUBMITTED


@run_async
async def test_allowed_resume_skips_real_permission_engine():
    log, call = [], make_call(state=ToolCallState.ALLOWED)
    agent = make_agent(MemoryTool(log, PermissionBehavior.DENY), [call])
    await collect(agent._execute_tool_call(call))
    assert [item[0] for item in log] == ["call"]


@run_async
async def test_concurrent_asks_are_deduplicated_by_broad_suggestion():
    log, calls = [], [make_call("one", 1), make_call("two", 2)]
    agent = make_agent(MemoryTool(log, PermissionBehavior.PASSTHROUGH), calls)
    events = await collect(agent._execute_concurrent_tool_calls(calls))
    prompts = [item for item in events if isinstance(item, RequireUserConfirmEvent)]
    assert len(prompts) == 1
    assert prompts[0].tool_calls[0].suggested_rules[0].tool_name == "MemoryTool"
    assert sorted(call.state for call in calls) == sorted([ToolCallState.ASKING, ToolCallState.PENDING])
    assert not any(item[0] == "call" for item in log)
