"""运行后 Markdown 记忆反思测试。"""

from __future__ import annotations

import asyncio

from src.memory.extraction_agents import (
    MemoryReflectionAgentPayload,
    SkillDraftPayload,
    SkillEvolutionAgentPayload,
)
from src.memory.markdown_store import MarkdownMemoryStore
from src.memory.reflection import (
    MemoryReflectionInput,
    reflect_completed_run_memory,
    reflect_completed_run_memory_with_agents,
)


def test_reflection_writes_user_preference_memory(tmp_path):
    store = MarkdownMemoryStore(root=tmp_path / "memory")

    result = reflect_completed_run_memory(
        MemoryReflectionInput(
            session_id="session-1",
            agent_id="agent-1",
            agent_name="Agent",
            user_message="以后默认用中文回答，解释保持简洁。",
            assistant_text="好的。",
        ),
        store=store,
    )

    assert result.ignored_reason == ""
    assert result.updates[0].scope == "user"
    assert result.updates[0].section == "Stable Preferences"
    content = (tmp_path / "memory" / "users" / "default.md").read_text(encoding="utf-8")
    assert "以后默认用中文回答，解释保持简洁。" in content
    assert "source: session-1" in content


def test_reflection_writes_agent_negative_memory(tmp_path):
    store = MarkdownMemoryStore(root=tmp_path / "memory")

    result = reflect_completed_run_memory(
        MemoryReflectionInput(
            session_id="session-1",
            agent_id="agent-1",
            agent_name="Agent",
            user_message="你以后不要再恢复旧 Plan Runtime。",
            assistant_text="明白。",
        ),
        store=store,
    )

    assert result.updates[0].scope == "agent"
    assert result.updates[0].section == "Negative Rules"
    content = (tmp_path / "memory" / "agents" / "agent-1.md").read_text(encoding="utf-8")
    assert "你以后不要再恢复旧 Plan Runtime。" in content


def test_reflection_writes_global_memory(tmp_path):
    store = MarkdownMemoryStore(root=tmp_path / "memory")

    result = reflect_completed_run_memory(
        MemoryReflectionInput(
            session_id="session-1",
            agent_id="agent-1",
            agent_name="Agent",
            user_message="整个工作台以后默认不要新增 memory runtime.kind。",
            assistant_text="收到。",
            workspace_id="workspace-1",
        ),
        store=store,
    )

    assert result.updates[0].scope == "global_preference"
    assert result.updates[0].section == "Negative Rules"
    content = (tmp_path / "memory" / "global" / "workspace-1.md").read_text(encoding="utf-8")
    assert "整个工作台以后默认不要新增 memory runtime.kind。" in content


def test_reflection_ignores_without_long_term_intent(tmp_path):
    result = reflect_completed_run_memory(
        MemoryReflectionInput(
            session_id="session-1",
            agent_id="agent-1",
            agent_name="Agent",
            user_message="帮我看一下这个函数。",
            assistant_text="可以。",
        ),
        store=MarkdownMemoryStore(root=tmp_path / "memory"),
    )

    assert result.ignored_reason == "no_long_term_intent"
    assert not (tmp_path / "memory").exists()


def test_reflection_ignores_sensitive_content(tmp_path):
    result = reflect_completed_run_memory(
        MemoryReflectionInput(
            session_id="session-1",
            agent_id="agent-1",
            agent_name="Agent",
            user_message="记住 token=secret-value-123456",
            assistant_text="不应该记。",
        ),
        store=MarkdownMemoryStore(root=tmp_path / "memory"),
    )

    assert result.ignored_reason == "sensitive_content"
    assert not (tmp_path / "memory").exists()


def test_reflection_deduplicates_existing_note(tmp_path):
    store = MarkdownMemoryStore(root=tmp_path / "memory")
    item = MemoryReflectionInput(
        session_id="session-1",
        agent_id="agent-1",
        agent_name="Agent",
        user_message="以后默认用中文回答。",
        assistant_text="好的。",
    )

    first = reflect_completed_run_memory(item, store=store)
    second = reflect_completed_run_memory(item, store=store)

    assert first.updates
    assert second.ignored_reason == "duplicate"
    content = (tmp_path / "memory" / "users" / "default.md").read_text(encoding="utf-8")
    assert content.count("以后默认用中文回答。") == 1


def test_reflection_with_agents_writes_model_memories(tmp_path, monkeypatch):
    captured = {}

    async def fake_memory_agent(**kwargs):
        captured.update(kwargs)
        memory_file = kwargs["memory_file"]
        add_tool = memory_file.tools()[1]
        await add_tool(
            section="Workspace Rules",
            note="整个工作台默认通过 hooks 和 tools 接入记忆，不新增 memory runtime.kind。",
            reason="全局架构规则。",
        )
        return MemoryReflectionAgentPayload(
            shouldWrite=True,
            reason="用户明确给出长期规则。",
            memories=[],
        )

    async def fake_skill_agent(**kwargs):
        return None

    monkeypatch.setattr("src.memory.reflection.run_memory_reflection_agent", fake_memory_agent)
    monkeypatch.setattr("src.memory.reflection.run_skill_evolution_agent", fake_skill_agent)
    store = MarkdownMemoryStore(root=tmp_path / "memory")
    store.write_global_memory("default", "# Summary\n\n已有规则：不要恢复旧记忆模块。")

    result = asyncio.run(
        reflect_completed_run_memory_with_agents(
            MemoryReflectionInput(
                session_id="session-1",
                agent_id="agent-1",
                agent_name="Agent",
                user_message="整个工作台以后默认记忆都通过 hook 和 tools 实现。",
                assistant_text="收到，我会按这个规则处理。",
            ),
            model_name="model",
            store=store,
        ),
    )

    assert result.ignored_reason == ""
    assert result.updates[0].scope == "global_preference"
    assert captured["memory_file"].scope == "global_preference"
    assert captured["memory_file"].file_path.endswith("/global/default.md")
    content = (tmp_path / "memory" / "global" / "default.md").read_text(encoding="utf-8")
    assert "不新增 memory runtime.kind" in content


def test_reflection_with_agents_returns_skill_candidate(tmp_path, monkeypatch):
    async def fake_memory_agent(**kwargs):
        return None

    async def fake_skill_agent(**kwargs):
        return SkillEvolutionAgentPayload(
            reusable=True,
            confidence=0.86,
            reason="本次工作形成了可复用流程。",
            draft=SkillDraftPayload(
                name="Memory Hook Design",
                description="设计并接入记忆 hooks 的流程。",
                trigger="当用户需要设计记忆模块 hooks 时使用。",
                content="1. 明确 hook 生命周期。\n2. 区分 Markdown 和节点记忆。",
                tags=["memory", "hooks"],
            ),
        )

    monkeypatch.setattr("src.memory.reflection.run_memory_reflection_agent", fake_memory_agent)
    monkeypatch.setattr("src.memory.reflection.run_skill_evolution_agent", fake_skill_agent)

    result = asyncio.run(
        reflect_completed_run_memory_with_agents(
            MemoryReflectionInput(
                session_id="session-1",
                agent_id="agent-1",
                agent_name="Agent",
                user_message="帮我设计一个可复用的记忆 hook 流程。",
                assistant_text="已完成设计。\n" + "实现步骤：" * 80,
            ),
            model_name="model",
            store=MarkdownMemoryStore(root=tmp_path / "memory"),
        ),
    )

    assert result.skill_candidate is not None
    assert result.skill_candidate.draft is not None
    assert result.skill_candidate.draft.name == "Memory Hook Design"


def test_reflection_with_agents_falls_back_to_rules(tmp_path, monkeypatch):
    async def failing_memory_agent(**kwargs):
        raise RuntimeError("model unavailable")

    async def fake_skill_agent(**kwargs):
        return None

    monkeypatch.setattr("src.memory.reflection.run_memory_reflection_agent", failing_memory_agent)
    monkeypatch.setattr("src.memory.reflection.run_skill_evolution_agent", fake_skill_agent)

    result = asyncio.run(
        reflect_completed_run_memory_with_agents(
            MemoryReflectionInput(
                session_id="session-1",
                agent_id="agent-1",
                agent_name="Agent",
                user_message="以后默认用中文回答。",
                assistant_text="好的。",
            ),
            model_name="model",
            store=MarkdownMemoryStore(root=tmp_path / "memory"),
        ),
    )

    assert result.updates
    content = (tmp_path / "memory" / "users" / "default.md").read_text(encoding="utf-8")
    assert "以后默认用中文回答。" in content
