import asyncio
from types import SimpleNamespace

from agentscope.message import ToolResultState

from src.server.routes.chat.plan_mode.manager import PlanModeManager
from src.server.routes.chat.plan_mode.middleware import PlanModeMiddleware


class DummyToolCall:
    id = "tool-1"
    name = "Write"
    input = '{"path": "x", "content": "y"}'


def test_system_prompt_contains_plan_banner_when_active() -> None:
    manager = PlanModeManager(
        session_id="session-1",
        run_id="run-1",
        agent_id="agent-1",
        agent_name="Agent",
        persist=False,
    )
    manager.enter(goal="制定计划")
    middleware = PlanModeMiddleware(manager)

    prompt = asyncio.run(middleware.on_system_prompt(SimpleNamespace(name="Agent"), "base"))

    assert "PLAN MODE is active" in prompt
    assert manager.context.current_plan_file in prompt
    assert "exact relative path" in prompt
    assert "Avoid broad Glob patterns" in prompt


def test_system_prompt_contains_build_hint_after_approval() -> None:
    manager = PlanModeManager(
        session_id="session-1",
        run_id="run-1",
        agent_id="agent-1",
        agent_name="Agent",
        persist=False,
    )
    manager.enter(goal="制定计划")
    manager.context.plan_active = False
    manager.context.approved = True
    middleware = PlanModeMiddleware(manager)

    prompt = asyncio.run(middleware.on_system_prompt(SimpleNamespace(name="Agent"), "base"))

    assert "BUILD mode" in prompt
    assert manager.context.current_plan_file in prompt
    assert "instead of searching the entire project" in prompt
    assert "FinalAnswerStart" in prompt


def test_on_acting_denies_mutating_tool() -> None:
    manager = PlanModeManager(
        session_id="session-1",
        run_id="run-1",
        agent_id="agent-1",
        agent_name="Agent",
        persist=False,
    )
    manager.enter(goal="制定计划")
    middleware = PlanModeMiddleware(manager)
    called_next = False

    async def run_middleware() -> list[object]:
        async def next_handler(**kwargs):
            nonlocal called_next
            called_next = True
            yield "unexpected"

        return [
            item
            async for item in middleware.on_acting(
                SimpleNamespace(name="Agent"),
                {"tool_call": DummyToolCall()},
                next_handler,
            )
        ]

    chunks = asyncio.run(run_middleware())

    assert called_next is False
    assert chunks[-1].state == ToolResultState.DENIED
    assert "PLAN mode" in chunks[-1].content[0].text
    denied_events = [
        event
        for event in manager.drain_runtime_events()
        if event["type"] == "PLAN_TOOL_DENIED"
    ]
    assert denied_events
    assert denied_events[0]["payload"]["toolName"] == "Write"
