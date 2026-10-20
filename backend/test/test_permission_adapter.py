"""真实 SDK 上验证平台薄适配器的并发、恢复和外部执行接点。"""

import asyncio
import json

import pytest
from agentscope.agent import Agent
from agentscope.event import (
    ConfirmResult, ExternalExecutionResultEvent, RequireExternalExecutionEvent,
    RequireUserConfirmEvent, ToolResultEndEvent, ToolResultStartEvent, UserConfirmResultEvent,
)
from agentscope.message import ToolCallState, ToolResultBlock, ToolResultState
from agentscope.middleware import MiddlewareBase
from agentscope.permission import PermissionBehavior, PermissionRule

from src.agents.permission_adapter import PlatformAgent, _call_context
from src.runtime.authorization import ActionSnapshot, AuthorizationDecision, ExecutionRecord, RunAuthorization
from test.authorization_fakes import ActingRecorder, MemoryTool, collect, make_agent, make_call, run_async


class MemoryService:
    """只在内存保存精确行动批准，用于接点测试而非生产实现。"""

    def __init__(self, verdict="allow"):
        self.verdict = verdict
        self.checked = []
        self.reserved = []
        self.approved = set()
        self.started = []
        self.finished = []
        self.cancelled = False

    async def authorize(self, action):
        await asyncio.sleep(0)
        self.checked.append(action)
        verdict = "allow" if action.fingerprint in self.approved else self.verdict
        return AuthorizationDecision(id=action.fingerprint, actionFingerprint=action.fingerprint,
                                     verdict=verdict, source="manual", reasonCode="test", reason="内存决定")

    async def reserve_execution(self, action, decision_id):
        if self.cancelled or action.fingerprint != decision_id or action.fingerprint in self.reserved:
            raise ValueError("无效或已消费")
        self.reserved.append(action.fingerprint)
        return ExecutionRecord(id=action.fingerprint, decisionId=decision_id, status="reserved")

    async def start_execution(self, action, decision_id):
        if self.cancelled or action.fingerprint not in self.reserved or action.fingerprint in self.started:
            raise ValueError("不能分发")
        self.started.append(action.fingerprint)
        return ExecutionRecord(id=action.fingerprint, decisionId=decision_id, status="running")

    async def finish_execution(self, action, decision_id, *, status):
        self.finished.append((decision_id, status))
        return True


async def build_action(tool, call_id, canonical):
    """确定性内存快照，不读任何真实目标。"""

    return ActionSnapshot(authorization=RunAuthorization(
        tenantId="local", workspaceId="w", projectId="p", sessionId="s", runId="r",
        actorId="a", capabilityVersion="1"), callId=call_id, toolName=tool.name,
        toolVersion="1", normalizedInputJson=canonical)


def platform_agent(tool, calls, service, **kwargs):
    return make_agent(tool, calls, PlatformAgent, authorization_service=service,
                      action_builder=build_action, **kwargs)


def assert_pair(events, call_id, state):
    """每个执行/拒绝只出现一对开始结束事件。"""

    assert [e.tool_call_id for e in events if isinstance(e, ToolResultStartEvent)] == [call_id]
    ends = [e for e in events if isinstance(e, ToolResultEndEvent)]
    assert len(ends) == 1 and ends[0].tool_call_id == call_id and ends[0].state == state


@run_async
async def test_allow_uses_canonical_parameters_and_preserves_native_events():
    log, call, service = [], make_call(), MemoryService()
    call.input = '{ "value" : 1 }'
    agent = platform_agent(MemoryTool(log), [call], service, middlewares=[ActingRecorder(log)])
    events = await collect(agent._execute_tool_call(call))
    assert_pair(events, call.id, ToolResultState.SUCCESS)
    assert log == [("acting", call.id), ("call", json.loads(service.checked[0].normalized_input_json))]
    assert len(service.reserved) == 1
    assert _call_context.get() is None


@pytest.mark.parametrize("verdict", ["allow", "needs_human", "deny"])
@run_async
async def test_concurrent_calls_keep_independent_context_and_never_deduplicate(verdict):
    log, calls, service = [], [make_call(str(n), n) for n in range(3)], MemoryService(verdict)
    agent = platform_agent(MemoryTool(log), calls, service)
    events = await collect(agent._execute_concurrent_tool_calls(calls))
    assert {a.call_id: json.loads(a.normalized_input_json)["value"] for a in service.checked} == {str(n): n for n in range(3)}
    prompts = [e for e in events if isinstance(e, RequireUserConfirmEvent)]
    if verdict == "needs_human":
        assert len(prompts) == 3
        assert all(not call.suggested_rules for call in calls)
    else:
        assert len([e for e in events if isinstance(e, ToolResultEndEvent)]) == 3
    assert len(service.reserved) == (3 if verdict == "allow" else 0)
    assert _call_context.get() is None


@run_async
async def test_real_confirmation_cannot_grant_new_input_or_broad_allow():
    log, call, service = [], make_call(), MemoryService("needs_human")
    agent = platform_agent(MemoryTool(log), [call], service)
    await collect(agent._execute_tool_call(call))
    service.approved.add(service.checked[0].fingerprint)
    confirmation = UserConfirmResultEvent(reply_id=agent.state.reply_id, confirm_results=[ConfirmResult(
        confirmed=True, tool_call=call.model_copy(update={"input": '{"value":2}'}),
        rules=[PermissionRule(tool_name=call.name, rule_content=None,
                              behavior=PermissionBehavior.ALLOW, source="legacy")],
    )])
    await collect(agent._handle_incoming_event(confirmation))
    assert call.state == ToolCallState.ALLOWED
    events = await collect(agent._execute_tool_call(call))
    assert isinstance(events[-1], RequireUserConfirmEvent)
    assert not log and not service.reserved
    assert agent.state.permission_context.allow_rules == {}


@run_async
async def test_unchanged_confirmation_resumes_once_then_new_call_asks():
    log, call, service = [], make_call(), MemoryService("needs_human")
    agent = platform_agent(MemoryTool(log), [call], service)
    await collect(agent._execute_tool_call(call))
    service.approved.add(service.checked[0].fingerprint)
    await collect(agent._handle_incoming_event(UserConfirmResultEvent(
        reply_id=agent.state.reply_id, confirm_results=[ConfirmResult(confirmed=True, tool_call=call)])))
    assert_pair(await collect(agent._execute_tool_call(call)), call.id, ToolResultState.SUCCESS)
    assert await collect(agent._execute_tool_call(call)) == []
    new_call = make_call("new")
    events = await collect(agent._execute_tool_call(new_call))
    assert isinstance(events[-1], RequireUserConfirmEvent)
    assert len(service.reserved) == 1 and log == [("call", {"value": 1})]


@run_async
async def test_allowed_is_rechecked_and_boundary_denial_wins():
    log, call, service = [], make_call(state=ToolCallState.ALLOWED), MemoryService("deny")
    agent = platform_agent(MemoryTool(log), [call], service)
    assert_pair(await collect(agent._execute_tool_call(call)), call.id, ToolResultState.DENIED)
    assert not log and not service.reserved and len(service.checked) == 1


@run_async
async def test_external_reserves_only_at_real_dispatch_and_keeps_native_result_pair():
    log, call, service = [], make_call(), MemoryService()
    agent = platform_agent(MemoryTool(log, external=True), [call], service,
                           middlewares=[ActingRecorder(log)])
    events = await collect(agent._execute_tool_call(call))
    assert isinstance(events[-1], RequireExternalExecutionEvent)
    assert not service.reserved and not log
    result = await agent.execute_authorized_external_tool_call(
        call.model_dump(mode="json"),
        lambda _: asyncio.sleep(0, result=ToolResultBlock(
            id=call.id, name=call.name, output=[], state=ToolResultState.SUCCESS)),
    )
    assert result.state == ToolResultState.SUCCESS
    assert len(service.reserved) == len(service.started) == 1
    assert service.finished == [(service.checked[0].fingerprint, "succeeded")]
    assert call.input == service.checked[0].normalized_input_json
    result_events = await collect(agent._handle_incoming_event(ExternalExecutionResultEvent(
        reply_id=agent.state.reply_id, execution_results=[ToolResultBlock(
            id=call.id, name=call.name, output=[], state=ToolResultState.SUCCESS)])))
    assert_pair(events + result_events, call.id, ToolResultState.SUCCESS)


@run_async
async def test_invalid_input_cannot_be_repaired_into_executable_action():
    log, call, service = [], make_call(), MemoryService()
    call.input = '{value:1}'
    agent = platform_agent(MemoryTool(log), [call], service)
    assert_pair(await collect(agent._execute_tool_call(call)), call.id, ToolResultState.ERROR)
    assert not service.checked and not log


@run_async
async def test_native_reasoning_is_inherited_and_runs_real_fake_model():
    assert PlatformAgent._reasoning is Agent._reasoning
    assert PlatformAgent._reasoning_impl is Agent._reasoning_impl
    assert PlatformAgent._execute_concurrent_tool_calls is Agent._execute_concurrent_tool_calls
    agent = platform_agent(MemoryTool([]), [], MemoryService())
    events = await collect(agent._reasoning())
    assert agent.model.requests
    assert any(getattr(e, "content", None) for e in events)


@run_async
async def test_acting_middleware_cannot_change_approved_input():
    class ChangeInput(MiddlewareBase):
        async def on_acting(self, agent, input_kwargs, next_handler):
            changed = input_kwargs["tool_call"].model_copy(update={"input": '{"value":99}'})
            async for item in next_handler(tool_call=changed):
                yield item

    log, call, service = [], make_call(), MemoryService()
    agent = platform_agent(MemoryTool(log), [call], service, middlewares=[ChangeInput()])
    assert_pair(await collect(agent._execute_tool_call(call)), call.id, ToolResultState.DENIED)
    assert not log and not service.reserved


@run_async
async def test_user_rejection_and_external_denial_never_execute():
    log, call, service = [], make_call(), MemoryService("needs_human")
    agent = platform_agent(MemoryTool(log), [call], service)
    await collect(agent._execute_tool_call(call))
    events = await collect(agent._handle_incoming_event(UserConfirmResultEvent(
        reply_id=agent.state.reply_id, confirm_results=[ConfirmResult(confirmed=False, tool_call=call)])))
    assert_pair(events, call.id, ToolResultState.DENIED)
    assert not log and not service.reserved
    external = make_call("external")
    denied = platform_agent(MemoryTool(log, external=True), [external], MemoryService("deny"))
    assert_pair(await collect(denied._execute_tool_call(external)), external.id, ToolResultState.DENIED)
    assert not log


@run_async
async def test_external_cancel_before_dispatch_and_duplicate_event_never_start():
    log, call, service = [], make_call(), MemoryService()
    agent = platform_agent(MemoryTool(log, external=True), [call], service)
    await collect(agent._execute_tool_call(call))
    service.cancelled = True
    async def executor(_):
        log.append("dispatched")
        return ToolResultBlock(id=call.id, name=call.name, output=[], state=ToolResultState.SUCCESS)
    for _ in range(2):
        result = await agent.execute_authorized_external_tool_call(call.model_dump(mode="json"), executor)
        assert result.state == ToolResultState.DENIED
    assert not log and not service.started


@run_async
async def test_external_connection_lost_records_unknown_and_cannot_replay():
    call, service = make_call(), MemoryService()
    agent = platform_agent(MemoryTool([], external=True), [call], service)
    await collect(agent._execute_tool_call(call))
    count = 0
    async def executor(_):
        nonlocal count
        count += 1
        raise ConnectionError("sent but reply lost")
    with pytest.raises(ConnectionError):
        await agent.execute_authorized_external_tool_call(call.model_dump(mode="json"), executor)
    assert service.finished == [(service.checked[0].fingerprint, "unknown")]
    repeated = await agent.execute_authorized_external_tool_call(call.model_dump(mode="json"), executor)
    assert repeated.state == ToolResultState.DENIED and count == 1


@run_async
async def test_actual_tool_error_is_unknown_instead_of_claiming_no_side_effect():
    class ErrorTool(MemoryTool):
        async def call(self, **kwargs):
            from agentscope.tool import ToolChunk
            self.log.append("partial side effect")
            return ToolChunk(content=[], state=ToolResultState.ERROR)
    log, service, call = [], MemoryService(), make_call()
    agent = platform_agent(ErrorTool(log), [call], service)
    assert_pair(await collect(agent._execute_tool_call(call)), call.id, ToolResultState.ERROR)
    assert log == ["partial side effect"]
    assert service.finished == [(service.checked[0].fingerprint, "unknown")]
