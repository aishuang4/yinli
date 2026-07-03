"""运行后 Markdown 记忆反思测试。"""

from __future__ import annotations

from src.memory.markdown_store import MarkdownMemoryStore
from src.memory.reflection import MemoryReflectionInput, reflect_completed_run_memory


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
