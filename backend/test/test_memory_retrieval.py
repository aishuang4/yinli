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
    # 节点索引压成单行：date | nodeId | title — summary
    assert "| node-1 |" in text
    assert "讨论了记忆模块。" in text


def test_memory_injection_prefers_summary_over_source_sections():
    """存在 # Summary 时不重抄被摘要的章节，只保留 Summary + 行为约束。"""

    class SummaryStore(FakeMarkdownStore):
        def read_agent_memory(self, agent_id):
            return type(
                "M",
                (),
                {
                    "content": "\n".join([
                        "# Summary",
                        "- Agent 默认中文、简洁。",
                        "# Stable Behaviors",
                        "- 以后默认用中文解释。",
                        "# Negative Rules",
                        "- 不要恢复旧 Plan Runtime。",
                    ]),
                },
            )()

    text = build_memory_injection_text(
        MemoryRetrievalContext(
            agent_id="agent-1",
            agent_name="Agent",
            include_user_markdown=False,
            include_global_markdown=False,
            include_node_tree=False,
        ),
        markdown_store=SummaryStore(),
    )

    assert "Agent 默认中文、简洁。" in text
    assert "不要恢复旧 Plan Runtime。" in text
    assert "以后默认用中文解释。" not in text


def test_memory_injection_dedupes_nodes_and_respects_budget():
    from src.memory import retrieval

    class ManyNodes:
        def tree(self, **kwargs):
            nodes = []
            for index in range(60):
                nodes.append({
                    "date": f"2026-07-{(index % 5) + 1:02d}",
                    "nodeId": f"node-{index}",
                    "title": "重复标题" if index % 2 == 0 else f"标题 {index}",
                    "summary": "摘要 " * 40,
                    "tags": ["t"],
                })
            return {"nodes": nodes}

    text = build_memory_injection_text(
        MemoryRetrievalContext(
            agent_id="agent-1",
            agent_name="Agent",
            include_user_markdown=False,
            include_global_markdown=False,
            include_agent_markdown=False,
        ),
        markdown_store=FakeMarkdownStore(),
        node_service=ManyNodes(),
    )

    assert text.count("重复标题") == 1
    assert text.count("\n- 2026-07-") <= retrieval.MEMORY_NODE_TREE_LIMIT
    assert len(text) <= retrieval.MEMORY_INJECTION_TOTAL_CHARS + 400


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
