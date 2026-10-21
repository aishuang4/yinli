"""已启用 Skill 正文的会话级保留：捕获、补回、预算与重建。"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from agentscope.message import AssistantMsg, TextBlock, ToolCallBlock, ToolResultBlock, UserMsg
from agentscope.message._block import ToolResultState
from agentscope.state import AgentState
from agentscope.tool import ToolResponse

from src.agents.session_context import carry_over_context
from src.server.routes.chat import skill_context_middleware as scm
from src.server.routes.chat.skill_context_middleware import (
    SkillContextMiddleware,
    activate_skill,
    activated_skills,
    ensure_activated_skills_in_context,
    missing_activated_skills,
    preload_scheduled_skills,
    restore_activated_skills,
)

SKILL_BODY = "# word-documents\n\n" + "\n".join(f"步骤 {i}: 用脚本处理段落 {i}。" for i in range(40))


class _FakeAgent:
    def __init__(self, name: str = "Smoke") -> None:
        self.name = name
        self.state = AgentState()
        self.toolkit = SimpleNamespace(builtin_skill_viewer=SimpleNamespace(tool=SimpleNamespace(name="Skill")))


def _skill_tool_call(name: str = "word-documents") -> ToolCallBlock:
    return ToolCallBlock(id="call-1", name="Skill", input=json.dumps({"skill": name}))


async def _viewer_handler(**kwargs):
    yield ToolResponse(content=[TextBlock(text=SKILL_BODY)], state=ToolResultState.SUCCESS)


def _run_acting(agent: _FakeAgent, tool_call: ToolCallBlock, handler=_viewer_handler) -> list:
    async def run():
        return [item async for item in SkillContextMiddleware().on_acting(agent, {"tool_call": tool_call}, handler)]

    return asyncio.run(run())


def test_scheduled_preload_uses_skill_name_and_full_body() -> None:
    agent = _FakeAgent()
    body = "先跑脚本\n" + ("步骤。" * 2000)
    skill = SimpleNamespace(
        id="external-abc",
        name="证书核对",
        content=body,
        resource_path="",
    )

    assert preload_scheduled_skills(agent, [skill]) == ["证书核对"]
    corpus = " ".join(
        str(getattr(block, "hint", "") or getattr(block, "text", "") or "")
        for msg in agent.state.context
        for block in (msg.content or [])
    )
    assert '<activated-skill name="证书核对">' in corpus
    assert "external-abc" not in corpus
    assert body in corpus
    assert "call the Skill tool" not in corpus


def test_on_acting_records_skill_viewer_output() -> None:
    agent = _FakeAgent()
    _run_acting(agent, _skill_tool_call())

    skills = activated_skills(agent)
    assert list(skills) == ["word-documents"]
    assert skills["word-documents"]["markdown"] == SKILL_BODY.strip()
    assert agent.state.middle_context[scm.PENDING_CHECK_KEY] is True


def test_on_acting_ignores_other_tools_and_failures() -> None:
    agent = _FakeAgent()
    _run_acting(agent, ToolCallBlock(id="c2", name="Read", input=json.dumps({"path": "x"})))

    async def failing(**kwargs):
        yield ToolResponse(content=[TextBlock(text="SkillNotFoundError: Skill 'x' not found.")], state=ToolResultState.ERROR)

    _run_acting(agent, _skill_tool_call("x"), failing)
    assert activated_skills(agent) == {}


def test_missing_detects_compressed_and_truncated_bodies() -> None:
    agent = _FakeAgent()
    activate_skill(agent, "word-documents", SKILL_BODY)
    # 完整 tool result 在 context 里：不缺失。
    agent.state.context.append(
        AssistantMsg(name="Smoke", content=[ToolResultBlock(id="call-1", name="Skill", output=SKILL_BODY)])
    )
    assert missing_activated_skills(agent) == []

    # 被 tool_result_limit 截断（只剩前半）：视为缺失。
    agent.state.context[-1].content[0].output = SKILL_BODY[: len(SKILL_BODY) // 2]
    assert missing_activated_skills(agent) == ["word-documents"]

    # 被压缩掉（context 清空、只剩 summary）：视为缺失。
    agent.state.context.clear()
    agent.state.summary = "之前讨论了 Word 文档处理。"
    assert missing_activated_skills(agent) == ["word-documents"]


def test_ensure_reinjects_hint_once_and_is_idempotent() -> None:
    agent = _FakeAgent()
    activate_skill(agent, "word-documents", SKILL_BODY)
    agent.state.context.append(UserMsg(name="user", content="继续"))

    assert ensure_activated_skills_in_context(agent) == ["word-documents"]
    hint_msg = agent.state.context[-1]
    assert hint_msg.role == "assistant"
    assert hint_msg.content[0].type == "hint"
    assert '<activated-skill name="word-documents">' in hint_msg.content[0].hint
    assert SKILL_BODY in hint_msg.content[0].hint
    assert agent.state.middle_context[scm.PENDING_CHECK_KEY] is False

    # 再次检查：正文已在 hint 里，不重复追加。
    assert ensure_activated_skills_in_context(agent) == []
    assert len(agent.state.context) == 2


def test_budget_evicts_oldest_and_clips_long_bodies(monkeypatch) -> None:
    monkeypatch.setattr(scm, "SKILL_CONTEXT_TOTAL_CHARS", 700)
    monkeypatch.setattr(scm, "SKILL_CONTEXT_PER_SKILL_CHARS", 300)
    agent = _FakeAgent()
    activate_skill(agent, "a", "A" * 400)
    activate_skill(agent, "b", "B" * 250)
    skills = activated_skills(agent)
    assert skills["a"]["markdown"].startswith("A" * 300)
    assert scm.TRUNCATED_NOTE in skills["a"]["markdown"]
    assert list(skills) == ["a", "b"]

    activate_skill(agent, "c", "C" * 250)
    # a(300+note) + b(250) + c(250) > 700：最早的 a 被淘汰。
    assert list(activated_skills(agent)) == ["b", "c"]


def test_on_compress_context_only_reinjects_when_context_changed() -> None:
    agent = _FakeAgent()
    activate_skill(agent, "word-documents", SKILL_BODY)
    agent.state.context.append(
        AssistantMsg(name="Smoke", content=[ToolResultBlock(id="call-1", name="Skill", output=SKILL_BODY)])
    )

    async def noop(**kwargs):
        return None

    asyncio.run(SkillContextMiddleware().on_compress_context(agent, {}, noop))
    assert len(agent.state.context) == 1

    async def compress(**kwargs):
        agent.state.context.clear()
        agent.state.summary = "summary"

    asyncio.run(SkillContextMiddleware().on_compress_context(agent, {}, compress))
    assert len(agent.state.context) == 1
    assert agent.state.context[0].content[0].type == "hint"


def test_on_reply_reinjects_after_carry_over_to_new_instance() -> None:
    old = _FakeAgent()
    activate_skill(old, "word-documents", SKILL_BODY)
    old.state.context.append(UserMsg(name="user", content="帮我写文档"))
    old.state.context.append(
        AssistantMsg(name="Smoke", content=[ToolResultBlock(id="call-1", name="Skill", output=SKILL_BODY)])
    )
    new = _FakeAgent()
    carry_over_context(old, new)
    assert activated_skills(new) == activated_skills(old)
    # 搬运后 tool result 仍在，不需要补回
    assert missing_activated_skills(new) == []

    # 模拟正文被压缩掉后新一轮开始
    new.state.context = [UserMsg(name="user", content="继续")]

    async def handler(**kwargs):
        yield "done"

    async def run():
        return [item async for item in SkillContextMiddleware().on_reply(new, {"inputs": None}, handler)]

    assert asyncio.run(run()) == ["done"]
    assert new.state.context[-1].content[0].type == "hint"


def test_restore_from_skill_run_records_respects_catalog_and_limit() -> None:
    agent = _FakeAgent()
    available = [
        SimpleNamespace(name="word-documents", content=SKILL_BODY),
        SimpleNamespace(name="yinli-charts", content="# charts\n画图"),
    ]
    restored = restore_activated_skills(
        agent,
        recent_skill_names=["yinli-charts", "removed-skill", "word-documents", "another"],
        available_skills=available,
        limit=3,
    )
    assert restored == ["word-documents", "yinli-charts"]
    # 最近使用的（yinli-charts）启用时间最晚，预算不足时最后被淘汰
    assert list(activated_skills(agent)) == ["word-documents", "yinli-charts"]
    # 不重复恢复
    assert restore_activated_skills(agent, recent_skill_names=["word-documents"], available_skills=available) == []
