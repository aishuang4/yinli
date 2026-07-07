"""记忆提炼内部工具测试。"""

from __future__ import annotations

import asyncio

from src.tools.memory_reflection import MemoryFileToolSet, SkillDraftPropose


def test_memory_read_returns_bound_file_content():
    state = {"content": "# Summary\n\n已有记忆。"}
    tool_set = _tool_set(state)
    read_tool = tool_set.tools()[0]

    result = asyncio.run(read_tool())
    text = result.content[0].text

    assert "/tmp/user.md" in text
    assert "已有记忆。" in text


def test_memory_add_updates_only_bound_file():
    state = {"content": "# Summary\n\n# Stable Preferences\n"}
    tool_set = _tool_set(state)
    add_tool = tool_set.tools()[1]

    result = asyncio.run(
        add_tool(
            section="Stable Preferences",
            note="以后默认用中文回答。",
            reason="用户长期偏好。",
        ),
    )

    assert "updated" in result.content[0].text
    assert "以后默认用中文回答。" in state["content"]
    assert tool_set.operations[0]["operation"] == "append"
    assert tool_set.operations[0]["filePath"] == "/tmp/user.md"


def test_memory_add_preserves_obsidian_entity_links():
    state = {"content": "# Summary\n\n# Workspace Rules\n"}
    tool_set = _tool_set(state)
    add_tool = tool_set.tools()[1]

    asyncio.run(
        add_tool(
            section="Workspace Rules",
            note="[[规则:禁止恢复旧 Plan Runtime]] 是全局约束。",
            reason="用户明确给出长期规则。",
        ),
    )

    assert "- [[规则:禁止恢复旧 Plan Runtime]] 是全局约束。" in state["content"]
    assert tool_set.operations[0]["note"] == "- [[规则:禁止恢复旧 Plan Runtime]] 是全局约束。"


def test_memory_update_replaces_exact_text():
    state = {"content": "# Summary\n\n- 以后默认用英文回答。\n"}
    tool_set = _tool_set(state)
    update_tool = tool_set.tools()[2]

    result = asyncio.run(
        update_tool(
            exactText="- 以后默认用英文回答。",
            replacementText="- 以后默认用中文回答。",
            reason="用户纠正偏好。",
        ),
    )

    assert "updated" in result.content[0].text
    assert "英文" not in state["content"]
    assert "中文" in state["content"]
    assert tool_set.operations[0]["operation"] == "update"


def test_memory_delete_removes_exact_text_once():
    state = {"content": "# Summary\n\n- 临时记忆。\n- 保留记忆。\n"}
    tool_set = _tool_set(state)
    delete_tool = tool_set.tools()[3]

    result = asyncio.run(
        delete_tool(
            exactText="- 临时记忆。",
            reason="用户要求删除。",
        ),
    )

    assert "updated" in result.content[0].text
    assert "临时记忆" not in state["content"]
    assert "保留记忆" in state["content"]
    assert tool_set.operations[0]["operation"] == "delete"


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


def _tool_set(state: dict[str, str]) -> MemoryFileToolSet:
    return MemoryFileToolSet(
        scope="user",
        owner_id="default",
        file_path="/tmp/user.md",
        read_content=lambda: state["content"],
        write_content=lambda content: state.update({"content": content}),
    )
