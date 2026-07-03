"""Markdown 记忆懒创建测试。"""

from __future__ import annotations

import asyncio

from src.memory.markdown_store import AGENT_MEMORY_TEMPLATE, MarkdownMemoryStore
from src.server.routes.agents import AgentCreate, Guardrails, create_agent


def test_read_agent_memory_returns_template_without_creating_file(tmp_path):
    store = MarkdownMemoryStore(root=tmp_path / "memory")

    doc = store.read_agent_memory("agent-1")

    assert doc.exists is False
    assert doc.content == AGENT_MEMORY_TEMPLATE
    assert not (tmp_path / "memory" / "agents" / "agent-1.md").exists()


def test_write_agent_memory_creates_file(tmp_path):
    store = MarkdownMemoryStore(root=tmp_path / "memory")

    doc = store.write_agent_memory("agent-1", "# Summary\n\n记住中文输出。")

    path = tmp_path / "memory" / "agents" / "agent-1.md"
    assert doc.exists is True
    assert path.exists()
    assert path.read_text(encoding="utf-8") == "# Summary\n\n记住中文输出。\n"


def test_agent_create_does_not_create_markdown_memory(tmp_path, monkeypatch):
    from src.memory import markdown_store

    monkeypatch.setattr(markdown_store, "BACKEND_ROOT", tmp_path)

    agent = asyncio.run(
        create_agent(
            AgentCreate(
                name="Memory Test Agent",
                guardrails=Guardrails(),
            ),
        ),
    )

    assert agent.name == "Memory Test Agent"
    assert not (tmp_path / "data" / "memory" / "agents" / f"{agent.id}.md").exists()
