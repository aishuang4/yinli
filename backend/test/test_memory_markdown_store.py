"""Markdown 记忆懒创建测试。"""

from __future__ import annotations

import asyncio

from src.memory.markdown_store import (
    AGENT_MEMORY_TEMPLATE,
    GLOBAL_MEMORY_TEMPLATE,
    USER_MEMORY_TEMPLATE,
    MarkdownMemoryStore,
)
from src.server.routes import memory as memory_routes
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


def test_read_user_memory_returns_template_without_creating_file(tmp_path):
    store = MarkdownMemoryStore(root=tmp_path / "memory")

    doc = store.read_user_memory("user-1")

    assert doc.scope == "user"
    assert doc.owner_id == "user-1"
    assert doc.exists is False
    assert doc.content == USER_MEMORY_TEMPLATE
    assert not (tmp_path / "memory" / "users" / "user-1.md").exists()


def test_write_user_memory_creates_file(tmp_path):
    store = MarkdownMemoryStore(root=tmp_path / "memory")

    doc = store.write_user_memory("user-1", "# Summary\n\n用户偏好中文。")

    path = tmp_path / "memory" / "users" / "user-1.md"
    assert doc.scope == "user"
    assert doc.exists is True
    assert path.exists()
    assert path.read_text(encoding="utf-8") == "# Summary\n\n用户偏好中文。\n"


def test_read_global_memory_returns_template_without_creating_file(tmp_path):
    store = MarkdownMemoryStore(root=tmp_path / "memory")

    doc = store.read_global_memory("workspace-1")

    assert doc.scope == "global_preference"
    assert doc.owner_id == "workspace-1"
    assert doc.exists is False
    assert doc.content == GLOBAL_MEMORY_TEMPLATE
    assert not (tmp_path / "memory" / "global" / "workspace-1.md").exists()


def test_write_global_memory_creates_file(tmp_path):
    store = MarkdownMemoryStore(root=tmp_path / "memory")

    doc = store.write_global_memory("workspace-1", "# Summary\n\n全局默认中文。")

    path = tmp_path / "memory" / "global" / "workspace-1.md"
    assert doc.scope == "global_preference"
    assert doc.exists is True
    assert path.exists()
    assert path.read_text(encoding="utf-8") == "# Summary\n\n全局默认中文。\n"


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


def test_user_markdown_route_uses_lazy_template_and_first_write(tmp_path, monkeypatch):
    from src.memory import markdown_store

    monkeypatch.setattr(markdown_store, "BACKEND_ROOT", tmp_path)

    read_response = asyncio.run(memory_routes.read_user_markdown_memory("user-1"))
    assert read_response["scope"] == "user"
    assert read_response["ownerId"] == "user-1"
    assert read_response["exists"] is False
    assert read_response["content"] == USER_MEMORY_TEMPLATE
    assert not (tmp_path / "data" / "memory" / "users" / "user-1.md").exists()

    write_response = asyncio.run(
        memory_routes.write_user_markdown_memory(
            "user-1",
            memory_routes.MarkdownMemoryUpdate(content="# Summary\n\n用户默认中文。"),
        ),
    )
    assert write_response["exists"] is True
    assert (tmp_path / "data" / "memory" / "users" / "user-1.md").exists()


def test_global_markdown_route_uses_lazy_template_and_first_write(tmp_path, monkeypatch):
    from src.memory import markdown_store

    monkeypatch.setattr(markdown_store, "BACKEND_ROOT", tmp_path)

    read_response = asyncio.run(memory_routes.read_global_markdown_memory("workspace-1"))
    assert read_response["scope"] == "global_preference"
    assert read_response["ownerId"] == "workspace-1"
    assert read_response["exists"] is False
    assert read_response["content"] == GLOBAL_MEMORY_TEMPLATE
    assert not (tmp_path / "data" / "memory" / "global" / "workspace-1.md").exists()

    write_response = asyncio.run(
        memory_routes.write_global_markdown_memory(
            "workspace-1",
            memory_routes.MarkdownMemoryUpdate(content="# Summary\n\n全局默认中文。"),
        ),
    )
    assert write_response["exists"] is True
    assert (tmp_path / "data" / "memory" / "global" / "workspace-1.md").exists()
