"""RunSteerMiddleware：中途补充消息必须进入 AgentScope 对话上下文而不是只出现一次。"""

from __future__ import annotations

import asyncio

from agentscope.state import AgentState

from src.server.routes.chat import steer_middleware
from src.server.routes.chat.steer_middleware import RunSteerMiddleware


class _FakeAgent:
    def __init__(self) -> None:
        self.state = AgentState()


def test_steer_is_written_to_context_and_prompt_untouched(monkeypatch) -> None:
    monkeypatch.setattr(steer_middleware, "current_chat_background_run_id", lambda: "run-1")
    monkeypatch.setattr(steer_middleware, "take_run_steers", lambda run_id: ["改用中文回答"])
    agent = _FakeAgent()

    prompt = asyncio.run(RunSteerMiddleware().on_system_prompt(agent, "base"))

    assert prompt == "base"
    assert len(agent.state.context) == 1
    msg = agent.state.context[0]
    assert msg.role == "user"
    assert "改用中文回答" in msg.get_text_content()


def test_steer_falls_back_to_prompt_without_state(monkeypatch) -> None:
    monkeypatch.setattr(steer_middleware, "current_chat_background_run_id", lambda: "run-1")
    monkeypatch.setattr(steer_middleware, "take_run_steers", lambda run_id: ["只看 docs 目录"])

    prompt = asyncio.run(RunSteerMiddleware().on_system_prompt(object(), "base"))

    assert "# 用户补充" in prompt and "只看 docs 目录" in prompt


def test_on_acting_does_not_consume_steers(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(steer_middleware, "current_chat_background_run_id", lambda: "run-1")
    monkeypatch.setattr(steer_middleware, "take_run_steers", lambda run_id: calls.append(run_id) or [])

    async def handler(**kwargs):
        yield "ok"

    async def run():
        return [item async for item in RunSteerMiddleware().on_acting(_FakeAgent(), {}, handler)]

    assert asyncio.run(run()) == ["ok"]
    assert calls == []
