"""Chat 会话级 AgentScope 上下文延续与 Agent 实例生命周期测试。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from agentscope.message import AssistantMsg, TextBlock, ToolCallBlock, UserMsg
from agentscope.message._block import ToolCallState
from agentscope.state import AgentState

from src.agents import factory as agent_factory
from src.agents.session_context import (
    SessionHistoryMessage,
    carry_over_context,
    seed_context_from_history,
    settle_awaiting_tool_calls,
)


def _fake_agent(name: str = "Agent", system_prompt: str = "prompt") -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        state=AgentState(),
        _system_prompt=system_prompt,
        model=SimpleNamespace(on_event=None),
    )


def _awaiting_ask_user(agent: SimpleNamespace) -> None:
    agent.state.reply_id = "reply-1"
    agent.state.context.append(UserMsg(name="user", content="帮我处理一下"))
    agent.state.context.append(
        AssistantMsg(
            id="reply-1",
            name=agent.name,
            content=[
                ToolCallBlock(id="call-1", name="AskUser", input="{}", state=ToolCallState.SUBMITTED),
            ],
        ),
    )
    assert agent.state.has_awaiting_tool_calls(agent.name)


def test_settle_awaiting_tool_calls_closes_pending_ask_user():
    """用户不回答 AskUser 直接发新消息时，等待中的 tool call 必须被关闭，否则 AgentScope 拒绝新输入。"""

    agent = _fake_agent()
    _awaiting_ask_user(agent)

    assert settle_awaiting_tool_calls(agent) == 1
    assert not agent.state.has_awaiting_tool_calls(agent.name)
    results = agent.state.context[-1].get_content_blocks("tool_result")
    assert results and results[0].id == "call-1"
    assert settle_awaiting_tool_calls(agent) == 0


def test_carry_over_context_moves_history_and_renames_assistant():
    previous = _fake_agent(name="旧名字")
    previous.state.context.extend([
        UserMsg(name="user", content="第一轮"),
        AssistantMsg(name="旧名字", content="第一轮回答"),
    ])
    previous.state.summary = "压缩摘要"
    target = _fake_agent(name="新名字")

    moved = carry_over_context(previous, target)

    assert moved == 2
    assert target.state.summary == "压缩摘要"
    assert target.state.context[1].name == "新名字"
    # 深拷贝：改新实例不影响旧实例
    target.state.context.append(UserMsg(name="user", content="第二轮"))
    assert len(previous.state.context) == 2


def test_seed_context_from_history_only_when_empty_and_clips():
    agent = _fake_agent()
    history = [
        SessionHistoryMessage(role="user", content="x" * 5000, file_names=("报表.xlsx",)),
        SessionHistoryMessage(role="assistant", content="回答"),
        SessionHistoryMessage(role="user", content="   "),
    ]

    assert seed_context_from_history(agent, history, max_chars=100) == 2
    first = agent.state.context[0]
    assert first.role == "user"
    text = "".join(block.text for block in first.content if isinstance(block, TextBlock))
    assert "报表.xlsx" in text and len(text) < 200
    assert agent.state.context[1].name == "Agent"
    # 已有上下文时不重复灌入
    assert seed_context_from_history(agent, history) == 0


@pytest.fixture
def factory_sandbox(monkeypatch):
    created: list[SimpleNamespace] = []

    def fake_agent(**kwargs):
        agent = SimpleNamespace(
            name=kwargs["name"],
            state=kwargs["state"],
            _system_prompt=kwargs["system_prompt"],
            model=SimpleNamespace(on_event=None),
        )
        created.append(agent)
        return agent

    primary = SimpleNamespace(default_model="fake-model")
    binding = SimpleNamespace(primary=primary, chain=[primary])
    monkeypatch.setattr(agent_factory, "binding_for_model", lambda _name: binding)
    monkeypatch.setattr(agent_factory, "create_model_chain", lambda *_a, **_k: SimpleNamespace(on_event=None))
    monkeypatch.setattr(agent_factory, "_create_default_toolkit", lambda **_k: object())
    monkeypatch.setattr(agent_factory, "LocalWorkspace", lambda **_k: object())
    monkeypatch.setattr(agent_factory, "Agent", fake_agent)
    agent_factory.clear_all_agents()
    yield created
    agent_factory.clear_all_agents()


def test_factory_reuses_instance_and_refreshes_prompt(factory_sandbox):
    first = agent_factory.get_or_create_agent(
        agent_id="a:s:v1", name="Agent", system_prompt="p1", session_context_key="a::s",
    )
    first.state.context.append(UserMsg(name="user", content="第一轮"))

    second = agent_factory.get_or_create_agent(
        agent_id="a:s:v1", name="Agent", system_prompt="p2", session_context_key="a::s",
    )

    assert second is first
    assert second._system_prompt == "p2"
    assert len(second.state.context) == 1
    assert len(factory_sandbox) == 1


def test_factory_carries_context_to_new_instance_and_drops_old_one(factory_sandbox):
    first = agent_factory.get_or_create_agent(
        agent_id="a:s:v1", name="Agent", system_prompt="p", session_context_key="a::s",
    )
    first.state.context.extend([
        UserMsg(name="user", content="第一轮"),
        AssistantMsg(name="Agent", content="回答"),
    ])
    _awaiting_ask_user(first)

    second = agent_factory.get_or_create_agent(
        agent_id="a:s:v2", name="Agent", system_prompt="p", session_context_key="a::s",
    )

    assert second is not first
    assert len(second.state.context) == 4
    assert not second.state.has_awaiting_tool_calls("Agent")
    assert not agent_factory.has_cached_agent("a:s:v1")
    assert agent_factory.has_cached_agent("a:s:v2")


def test_factory_forks_snapshot_when_instance_is_busy(factory_sandbox):
    first = agent_factory.get_or_create_agent(
        agent_id="a:s:v1", name="Agent", system_prompt="p", session_context_key="a::s",
    )
    first.state.context.append(UserMsg(name="user", content="第一轮"))
    agent_factory.mark_agent_busy(first, "run-1")

    second = agent_factory.get_or_create_agent(
        agent_id="a:s:v1", name="Agent", system_prompt="p", session_context_key="a::s",
    )

    assert second is not first
    assert len(second.state.context) == 1
    # 忙碌实例不被释放
    assert agent_factory.has_cached_agent("a:s:v1")
    agent_factory.mark_agent_idle(first)


def test_factory_evicts_idle_instances_but_keeps_awaiting_ones(factory_sandbox):
    idle = agent_factory.get_or_create_agent(agent_id="idle", name="Agent", system_prompt="p")
    waiting = agent_factory.get_or_create_agent(agent_id="waiting", name="Agent", system_prompt="p")
    _awaiting_ask_user(waiting)
    agent_factory._agent_last_used[agent_factory._cache_key("idle")] = 0.0
    agent_factory._agent_last_used[agent_factory._cache_key("waiting")] = 0.0

    evicted = agent_factory._evict_idle_agents(now=agent_factory.AGENT_CACHE_IDLE_SECONDS + 1)

    assert evicted == [agent_factory._cache_key("idle")]
    assert not agent_factory.has_cached_agent("idle")
    assert agent_factory.has_cached_agent("waiting")
    assert idle is not None


def test_factory_lru_cap_evicts_oldest_unreferenced(factory_sandbox):
    for index in range(5):
        agent_factory.get_or_create_agent(agent_id=f"agent-{index}", name="Agent", system_prompt="p")
    # 创建阶段会用真实 monotonic 时间做闲置判断，全部创建完再改写 LRU 时间戳。
    for index in range(5):
        agent_factory._agent_last_used[agent_factory._cache_key(f"agent-{index}")] = float(index)

    evicted = agent_factory._evict_idle_agents(now=10.0, max_size=3, idle_seconds=10_000)

    assert evicted == [
        agent_factory._cache_key("agent-0"),
        agent_factory._cache_key("agent-1"),
    ]
    assert len(agent_factory._agent_cache) == 3
