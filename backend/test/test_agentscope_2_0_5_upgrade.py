"""AgentScope 2.0.5 升级兼容回归。"""

import asyncio
from types import SimpleNamespace

import pytest
from agentscope.agent import Agent
from agentscope.event import ModelCallEndEvent
from agentscope.message import TextBlock
from agentscope.model import ChatResponse, ChatUsage
from agentscope.permission import (
    AdditionalWorkingDirectory,
    PermissionBehavior,
    PermissionContext,
    PermissionEngine,
    PermissionMode,
    PermissionRule,
)

from src.agents import factory as agent_factory
from src.agents.agentscope_usage_middleware import ProviderCacheUsageMiddleware
from src.agents.runtime_types import AgentGuardrails
from src.server.routes.chat.background_runs import (
    attach_chat_background_run_task,
    cancel_chat_background_run,
    relay_chat_background_run_events,
    start_chat_background_run,
    subscribe_chat_background_run,
)
from src.server.routes.chat.helpers import source_event
from src.server.routes.chat.leader_execution import execute_and_finalize_leader_reply
from src.server.routes.chat.leader_stream import (
    LeaderStreamState,
    _should_attempt_final_answer_recovery,
)
from src.server.routes.chat import trace_recorder
from src.tools.scoped_edit import ScopedEdit
from src.tools.scoped_write import ScopedWrite


class _FakeAgentScopeEvent:
    def __init__(self, payload: dict):
        self.payload = payload

    def model_dump(self, mode: str = "json") -> dict:
        return dict(self.payload)


def _chat_response(*, created: int, hit: int, is_last: bool = True) -> ChatResponse:
    return ChatResponse(
        content=[TextBlock(text="done")],
        is_last=is_last,
        usage=ChatUsage(
            input_tokens=20,
            output_tokens=4,
            time=0.01,
            cache_creation_input_tokens=created,
            cache_input_tokens=hit,
        ),
    )


@pytest.mark.parametrize("streaming", [False, True])
def test_provider_cache_usage_middleware_preserves_native_reasoning_events(streaming: bool):
    """middleware 同时支持流式和非流式响应，只扩展结束事件 metadata。"""

    async def run_case():
        middleware = ProviderCacheUsageMiddleware()
        agent = SimpleNamespace(state=SimpleNamespace(reply_id="reply-1"))

        async def model_handler(**_kwargs):
            if not streaming:
                return _chat_response(created=12, hit=30)

            async def chunks():
                yield _chat_response(created=0, hit=0, is_last=False)
                yield _chat_response(created=12, hit=30)

            return chunks()

        response = await middleware.on_model_call(
            agent=agent,
            input_kwargs={},
            next_handler=model_handler,
        )
        if streaming:
            response = [chunk async for chunk in response]

        async def reasoning_handler(**_kwargs):
            yield ModelCallEndEvent(
                reply_id="reply-1",
                input_tokens=20,
                output_tokens=4,
                metadata={"existing": True},
            )

        events = [
            event
            async for event in middleware.on_reasoning(
                agent=agent,
                input_kwargs={},
                next_handler=reasoning_handler,
            )
        ]
        return response, events[0], middleware

    response, event, middleware = asyncio.run(run_case())

    assert response
    assert event.metadata["existing"] is True
    assert event.metadata["cache_creation_input_tokens"] == 12
    assert event.metadata["cache_input_tokens"] == 30
    assert event.metadata["provider_cache"] == {"created_tokens": 12, "hit_tokens": 30}
    assert middleware._usage_by_reply == {}


def test_agentscope_reasoning_implementation_is_not_monkeypatched():
    """升级后必须运行 AgentScope 2.0.5 原生 reasoning 主循环。"""

    assert Agent._reasoning_impl.__module__ == "agentscope.agent._agent"


@pytest.mark.parametrize("tool_class", [ScopedWrite, ScopedEdit])
def test_scoped_write_tools_use_async_permission_rules(tmp_path, tool_class):
    """文件工具的规则匹配和建议生成必须返回实际值而非 coroutine。"""

    async def run_case():
        tool = tool_class(base_dir=str(tmp_path), scope_dir=str(tmp_path))
        tool_input = {"file_path": "docs/out.txt"}
        matched = await tool.match_rule(f"{tmp_path}/docs/**", tool_input)
        suggestions = await tool.generate_suggestions(tool_input)
        return matched, suggestions

    matched, suggestions = asyncio.run(run_case())

    assert matched is True
    assert suggestions
    assert suggestions[0].rule_content == f"{tmp_path}/docs/**"


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (PermissionMode.DEFAULT, PermissionBehavior.ASK),
        (PermissionMode.ACCEPT_EDITS, PermissionBehavior.ALLOW),
        (PermissionMode.EXPLORE, PermissionBehavior.DENY),
        (PermissionMode.DONT_ASK, PermissionBehavior.DENY),
        (PermissionMode.BYPASS, PermissionBehavior.ALLOW),
    ],
)
def test_scoped_write_permission_modes(tmp_path, mode: PermissionMode, expected: PermissionBehavior):
    """2.0.5 PermissionEngine 的五种模式不会因异步 override 发生误判。"""

    working_directories = (
        {str(tmp_path): AdditionalWorkingDirectory(path=str(tmp_path), source="test")}
        if mode == PermissionMode.ACCEPT_EDITS
        else {}
    )
    context = PermissionContext(mode=mode, working_directories=working_directories)
    decision = asyncio.run(PermissionEngine(context).check_permission(
        ScopedWrite(base_dir=str(tmp_path), scope_dir=str(tmp_path)),
        {"file_path": "docs/out.txt", "content": "value"},
    ))

    assert decision.behavior == expected


@pytest.mark.parametrize(
    ("rule_bucket", "behavior"),
    [
        ("allow_rules", PermissionBehavior.ALLOW),
        ("deny_rules", PermissionBehavior.DENY),
        ("ask_rules", PermissionBehavior.ASK),
    ],
)
def test_scoped_write_permission_rule_precedence(tmp_path, rule_bucket: str, behavior: PermissionBehavior):
    """allow、deny、ask 规则均按解析后的 Project 路径匹配。"""

    rule = PermissionRule(
        tool_name="Write",
        rule_content=f"{tmp_path}/docs/**",
        behavior=behavior,
        source="test",
    )
    context = PermissionContext(mode=PermissionMode.DEFAULT, **{rule_bucket: {"Write": [rule]}})
    decision = asyncio.run(PermissionEngine(context).check_permission(
        ScopedWrite(base_dir=str(tmp_path), scope_dir=str(tmp_path)),
        {"file_path": "docs/out.txt", "content": "value"},
    ))

    assert decision.behavior == behavior


def test_agent_factory_uses_2_0_5_workspace_middleware_and_injection_config(monkeypatch, tmp_path):
    """工厂使用 host_workdir，并以上海时区、不外发事件的方式启用 runtime state 注入。"""

    captured: dict = {}

    def fake_docker_workspace(**kwargs):
        captured["workspace"] = kwargs
        return object()

    def fake_agent(**kwargs):
        captured["agent"] = kwargs
        return SimpleNamespace(**kwargs)

    monkeypatch.setattr(agent_factory, "create_model_chain", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(agent_factory, "_create_default_toolkit", lambda **_kwargs: object())
    monkeypatch.setattr(agent_factory, "DockerWorkspace", fake_docker_workspace)
    monkeypatch.setattr(agent_factory, "Agent", fake_agent)

    runtime_id = "upgrade-factory-test"
    agent_factory.invalidate_agent(runtime_id)
    agent_factory.get_or_create_agent(
        agent_id=runtime_id,
        guardrails=AgentGuardrails(
            filesystem_scope=str(tmp_path),
            execution_backend="docker",
            persistent_sandbox=True,
        ),
    )

    assert "host_workdir" in captured["workspace"]
    assert "workdir" not in captured["workspace"]
    # 时间 / 上下文长度用原生 HintBlock 注入，system prompt 保持稳定；不外发 HintBlockEvent。
    injection = captured["agent"]["injection_config"]
    assert injection.inject_runtime_state is True
    assert injection.timezone == "Asia/Shanghai"
    assert injection.emit_hint_event is False
    assert "%Z" not in injection.time_format
    assert any(
        isinstance(item, ProviderCacheUsageMiddleware)
        for item in captured["agent"]["middlewares"]
    )
    agent_factory.invalidate_agent(runtime_id)


def test_interrupted_reply_fails_instead_of_completing():
    """非应用取消场景的 interrupted Reply 必须进入统一失败收口。"""

    class InterruptedAgent:
        async def reply_stream(self, _input):
            yield _FakeAgentScopeEvent({
                "type": "REPLY_END",
                "reply_id": "reply-interrupted",
                "finished_reason": "interrupted",
            })

    async def run_case():
        state = LeaderStreamState()
        events = [
            event
            async for event in execute_and_finalize_leader_reply(
                agent=InterruptedAgent(),
                input_event=object(),
                state=state,
                session_id="session-interrupted",
                persist=False,
                agent_id="agent-1",
                agent_name="Agent",
                model_name="model",
                guardrails=AgentGuardrails(),
                user_message="run",
                stream_scope="single",
                skill_runs=[],
            )
        ]
        return state, events

    state, events = asyncio.run(run_case())
    runtime_types = [event.data.get("type") for event in events if event.event_type != "ERROR"]

    assert state.reply_finished_reason == "interrupted"
    assert "AGENT_FAILED" in runtime_types
    assert "AGENT_COMPLETED" not in runtime_types
    assert "RUN_FAILED" in runtime_types
    assert any(event.event_type == "ERROR" for event in events)


def test_interrupted_reply_trace_is_failed(monkeypatch):
    """原始 AgentScope Trace 也必须保留 interrupted 失败语义。"""

    records: list[dict] = []
    monkeypatch.setattr(trace_recorder, "_record_run_event", lambda **kwargs: records.append(kwargs))

    trace_recorder._record_agent_scope_event(
        session_id="session-interrupted-trace",
        event_type="REPLY_END",
        event_data={
            "reply_id": "reply-interrupted",
            "finished_reason": "interrupted",
        },
        persist=True,
        scope="single",
        agent_id="agent-1",
        agent_name="Agent",
    )

    assert records[0]["status"] == "failed"
    assert records[0]["title"] == "Reply interrupted"
    assert records[0]["payload"]["finished_reason"] == "interrupted"


def test_application_cancel_remains_authoritative_when_agentscope_emits_interrupted():
    """SDK 吞掉 CancelledError 后发出的 interrupted 不能把已取消 Run 改回失败或完成。"""

    started = asyncio.Event()

    class CancellableAgent:
        async def reply_stream(self, _input):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                yield _FakeAgentScopeEvent({
                    "type": "REPLY_END",
                    "reply_id": "reply-cancelled",
                    "finished_reason": "interrupted",
                })

    async def run_case():
        async def factory():
            state = LeaderStreamState()
            async for event in execute_and_finalize_leader_reply(
                agent=CancellableAgent(),
                input_event=object(),
                state=state,
                session_id="session-cancelled",
                persist=False,
                agent_id="agent-1",
                agent_name="Agent",
                model_name="model",
                guardrails=AgentGuardrails(),
                user_message="run",
                stream_scope="single",
                skill_runs=[],
            ):
                yield event

        run = await start_chat_background_run(
            session_id="session-cancelled",
            factory=factory,
            persist=False,
        )
        await started.wait()
        await cancel_chat_background_run(run.id)
        assert run.async_task is not None
        await run.async_task
        return run

    run = asyncio.run(run_case())

    assert run.status == "cancelled"
    assert run.completed_at is not None


def test_cancel_targets_hitl_resume_task_and_closes_existing_subscriber():
    """HITL 恢复任务接管 Run 后，取消必须中止它并向原订阅发送终态。"""

    async def run_case():
        async def initial_factory():
            yield source_event("HITL_REQUIRED", {
                "interactionId": "interaction-resume-cancel",
                "type": "REQUIRE_EXTERNAL_EXECUTION",
                "toolCalls": [],
            })

        run = await start_chat_background_run(
            session_id="session-resume-cancel",
            factory=initial_factory,
            persist=False,
        )
        assert run.async_task is not None
        await run.async_task
        assert run.status == "waiting_user"

        resume_started = asyncio.Event()
        resume_cancelled = asyncio.Event()

        async def resume_events():
            resume_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                resume_cancelled.set()
                raise
            if False:
                yield source_event("ERROR", {"message": "unreachable"})

        async def drain_resume():
            async for _ in relay_chat_background_run_events(run.id, resume_events()):
                pass

        resume_task = asyncio.create_task(drain_resume())
        assert await attach_chat_background_run_task(run.id, resume_task) is True
        await resume_started.wait()

        async def collect_frames():
            return [
                frame
                async for frame in subscribe_chat_background_run(run.id, after_seq=0)
            ]

        collector = asyncio.create_task(collect_frames())
        await asyncio.sleep(0)
        await cancel_chat_background_run(run.id)

        with pytest.raises(asyncio.CancelledError):
            await resume_task
        frames = await collector
        return run, resume_cancelled.is_set(), frames

    run, resume_cancelled, frames = asyncio.run(run_case())

    assert resume_cancelled is True
    assert run.status == "cancelled"
    assert run.completed_at is not None
    assert any('"outcome":"cancelled"' in frame for frame in frames)
    assert any('"done":true' in frame for frame in frames)


def test_interrupted_reply_never_starts_final_answer_recovery():
    """取消后的 interrupted 即使已有思考和工具记录，也不能再次调用模型。"""

    state = LeaderStreamState(
        assistant_thinking="已经规划并开始执行工具",
        assistant_tool_calls=[SimpleNamespace()],
        reply_finished_reason="interrupted",
    )

    assert _should_attempt_final_answer_recovery(state) is False
