"""运行前记忆注入测试。"""

from __future__ import annotations

from src.memory.retrieval import MemoryRetrievalContext, build_memory_injection_text


def test_memory_injection_includes_markdown_sections_and_node_tree():
    text = build_memory_injection_text(
        MemoryRetrievalContext(agent_id="agent-1", agent_name="Agent"),
        markdown_store=FakeMarkdownStore(),
        node_service=FakeNodeService(),
    )

    assert "# Memory" in text
    assert "Global preference Markdown memory" in text
    assert "整个工作台默认用中文。" in text
    assert "User Markdown memory" in text
    assert "用户喜欢简洁答案。" in text
    assert "Agent Markdown memory" in text
    assert "Stable Behaviors" in text
    assert "以后默认用中文解释。" in text
    assert "Negative Rules" in text
    assert "不要恢复旧 Plan Runtime。" in text
    assert "memory_node_tree" in text
    assert "nodeId: node-1" in text
    assert "summary: 讨论了记忆模块。" in text


def test_memory_injection_returns_tool_hint_when_empty():
    text = build_memory_injection_text(
        MemoryRetrievalContext(agent_id="agent-1", agent_name="Agent"),
        markdown_store=EmptyMarkdownStore(),
        node_service=EmptyNodeService(),
    )

    assert "当前没有可注入的长期记忆详情" in text
    assert "memory_node_query" in text


class FakeMarkdownStore:
    def read_global_memory(self, workspace_id):
        return type(
            "Doc",
            (),
            {
                "content": """# Summary

整个工作台默认用中文。

# Stable Preferences

# Workspace Rules

# Corrections

# Negative Rules

# Source References
""",
            },
        )()

    def read_user_memory(self, user_id):
        return type(
            "Doc",
            (),
            {
                "content": """# Summary

用户喜欢简洁答案。

# Stable Preferences

# Profile

# Corrections

# Negative Rules

# Source References
""",
            },
        )()

    def read_agent_memory(self, agent_id):
        return type(
            "Doc",
            (),
            {
                "content": """# Summary

<!-- ignore -->

# Stable Behaviors

以后默认用中文解释。

# Corrections

# Negative Rules

不要恢复旧 Plan Runtime。

# Source References
""",
            },
        )()


class EmptyMarkdownStore:
    def read_global_memory(self, workspace_id):
        return type("Doc", (), {"content": "# Summary\n\n# Stable Preferences\n\n# Workspace Rules\n\n# Corrections\n\n# Negative Rules\n"})()

    def read_user_memory(self, user_id):
        return type("Doc", (), {"content": "# Summary\n\n# Stable Preferences\n\n# Profile\n\n# Corrections\n\n# Negative Rules\n"})()

    def read_agent_memory(self, agent_id):
        return type("Doc", (), {"content": "# Summary\n\n# Stable Behaviors\n\n# Corrections\n\n# Negative Rules\n"})()


class FakeNodeService:
    def tree(self, **kwargs):
        return {
            "nodes": [
                {
                    "nodeId": "node-1",
                    "date": "2026-07-03",
                    "agentId": "agent-1",
                    "title": "记忆模块",
                    "summary": "讨论了记忆模块。",
                    "tags": ["memory"],
                },
            ],
        }


class EmptyNodeService:
    def tree(self, **kwargs):
        return {"nodes": []}
