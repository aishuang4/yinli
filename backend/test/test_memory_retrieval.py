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
