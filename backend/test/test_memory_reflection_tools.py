"""记忆提炼内部工具测试。"""

from __future__ import annotations

import asyncio

from src.tools.memory_reflection import MemoryContextRead, MemoryOperationPropose, SkillDraftPropose


def test_memory_context_read_returns_existing_context():
    tool = MemoryContextRead("User memory:\n## Summary\n默认中文")

    result = asyncio.run(tool(existing="ignored"))
    text = result.content[0].text

    assert "默认中文" in text


def test_memory_operation_propose_records_candidate_without_writing():
    tool = MemoryOperationPropose()

    result = asyncio.run(
        tool(
            operation="append",
            scope="user",
            section="Stable Preferences",
            note="以后默认用中文回答。",
            confidence=0.9,
            reason="用户明确长期偏好。",
        ),
    )

    assert "候选已提交" in result.content[0].text
    assert tool.operations == [
        {
            "operation": "append",
            "scope": "user",
            "section": "Stable Preferences",
            "note": "以后默认用中文回答。",
            "confidence": 0.9,
            "reason": "用户明确长期偏好。",
        },
    ]


def test_skill_draft_propose_records_candidate_without_registering():
    tool = SkillDraftPropose()

    result = asyncio.run(
        tool(
            reusable=True,
            confidence=0.88,
            reason="形成可复用流程。",
            name="Memory Hook Design",
            description="设计记忆 hook。",
            trigger="需要规划记忆 hook 时使用。",
            content="1. 明确生命周期。\n2. 接入工具。",
            tags=["memory", "hooks"],
        ),
    )

    assert "Skill 草稿候选已提交" in result.content[0].text
    assert tool.drafts[0]["reusable"] is True
    assert tool.drafts[0]["draft"]["name"] == "Memory Hook Design"
    assert tool.drafts[0]["draft"]["tags"] == ["memory", "hooks"]
