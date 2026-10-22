"""运行前记忆注入测试。"""

from __future__ import annotations

from src.memory.legacy.injection import (
    MEMORY_RECALL_MIN_SCORE,
    MemoryRetrievalContext,
    build_memory_injection_text,
    build_memory_recall,
)
from src.memory.retrieval import build_habit_and_professional_prompt


def test_live_injection_is_habits_and_professional_only():
    class LiveStore:
        def read_habits(self, user_id):
            return type("Doc", (), {"content": "喜欢喝咖啡"})()

        def read_professional(self, agent_id):
            return type("Doc", (), {"content": "先读再改"})()

    text = build_habit_and_professional_prompt(
        user_id="default",
        agent_id="agent-1",
        markdown_store=LiveStore(),
    )
    assert "# Habits" in text
    assert "喜欢喝咖啡" in text
    assert "# Professional" in text
    assert "先读再改" in text
    assert "<recalled-memory>" not in text
    assert "memory_node_query" not in text
    assert "30 天" not in text


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
    assert "memory_node_query" in text
    # 节点索引压成单行、只留标题：date | nodeId | title；摘要按需用 memory_node_query 读取
    assert "| node-1 | 记忆模块" in text
    assert "讨论了记忆模块。" not in text


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
    from src.memory.legacy import injection as retrieval

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
    def read_global_memory(self):
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
    def read_global_memory(self):
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


class RecallNodeService:
    def __init__(self):
        self.calls: list[dict] = []

    def query(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "results": [
                {"nodeId": "n-same", "date": "2026-09-01", "agentId": "agent-1", "title": "本会话", "summary": "x",
                 "sourceSessionId": "s-current", "rankScore": 3.0},
                {"nodeId": "n-hit", "date": "2026-08-30", "agentId": "agent-1", "title": "Q3 销售表按地区汇总",
                 "summary": "帮我把 Q3 销售表按地区汇总 → 已生成 q3_by_region.xlsx", "sourceSessionId": "s-old", "rankScore": 2.1},
                {"nodeId": "n-dup", "date": "2026-08-29", "agentId": "agent-2", "title": "q3 销售表按地区汇总",
                 "summary": "重复标题", "sourceSessionId": "s-old2", "rankScore": 1.5},
                {"nodeId": "n-other", "date": "2026-08-28", "agentId": "agent-2", "title": "合同审阅",
                 "summary": "审阅了合同", "sourceSessionId": "s-old3", "rankScore": 1.0},
                {"nodeId": "n-weak", "date": "2026-08-27", "agentId": "agent-1", "title": "无关",
                 "summary": "无关", "sourceSessionId": "s-old4", "rankScore": MEMORY_RECALL_MIN_SCORE - 0.01},
            ],
            "meta": {},
        }


def test_memory_recall_filters_current_session_duplicates_and_weak_scores():
    service = RecallNodeService()
    result = build_memory_recall(
        MemoryRetrievalContext(agent_id="agent-1", agent_name="Agent"),
        query_text="再帮我把 Q3 销售表按地区汇总一下",
        exclude_session_id="s-current",
        node_service=service,
    )

    assert service.calls[0]["mark_used"] is False
    assert service.calls[0]["agent_id"] is None
    assert result.node_ids == ["n-hit", "n-other"]
    assert result.candidate_count == 5
    assert "<recalled-memory>" in result.text
    assert "n-hit | Q3 销售表按地区汇总 — 帮我把 Q3" in result.text
    # 其他 Agent 的节点标出来源 Agent
    assert "n-other (agent-2) | 合同审阅" in result.text
    assert "n-same" not in result.text and "n-weak" not in result.text


def test_memory_recall_skips_short_queries_and_swallows_errors():
    service = RecallNodeService()
    short = build_memory_recall(
        MemoryRetrievalContext(agent_id="agent-1", agent_name="Agent"),
        query_text="继续",
        node_service=service,
    )
    assert short.text == "" and service.calls == []

    class Broken:
        def query(self, **kwargs):
            raise RuntimeError("vector down")

    failed = build_memory_recall(
        MemoryRetrievalContext(agent_id="agent-1", agent_name="Agent"),
        query_text="帮我把销售表按地区汇总",
        node_service=Broken(),
    )
    assert failed.text == "" and failed.node_ids == []


def test_rank_terms_split_chinese_into_bigrams():
    from src.memory.nodes import _rank_terms

    terms = _rank_terms("帮我把Q3销售表汇总 openpyxl")
    assert "销售" in terms and "售表" in terms and "q3" in terms and "openpyxl" in terms
