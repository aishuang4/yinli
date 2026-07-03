"""记忆提炼提示词测试。"""

from __future__ import annotations

import json

from src.memory import extraction_agents


def test_memory_reflection_prompt_requires_chinese_output():
    prompt = extraction_agents._build_memory_reflection_system_prompt("user")

    assert "必须使用中文记录" in prompt
    assert "英文技术名词" in prompt
    assert "代码标识符" in prompt
    assert "note" in prompt
    assert "reason" in prompt


def test_memory_reflection_user_prompt_sets_chinese_policy():
    payload = json.loads(
        extraction_agents._build_memory_reflection_user_prompt(
            user_message="以后默认中文。",
            assistant_text="好的。",
            agent_id="agent-1",
            agent_name="Agent",
            memory_file=FakeMemoryFile(),
        ),
    )

    assert payload["policy"]["outputLanguage"] == "zh-CN"
    assert payload["policy"]["notesAndReasonsMustBeChinese"] is True


def test_skill_evolution_prompt_requires_chinese_draft():
    prompt = extraction_agents._build_skill_evolution_system_prompt()

    assert "所有 Skill 草稿字段必须使用中文记录" in prompt
    assert "中文 SKILL.md 风格操作说明" in prompt


def test_skill_evolution_user_prompt_sets_chinese_policy():
    payload = json.loads(
        extraction_agents._build_skill_evolution_user_prompt(
            user_message="总结这个流程。",
            assistant_text="已完成。",
            agent_id="agent-1",
            agent_name="Agent",
        ),
    )

    assert payload["policy"]["outputLanguage"] == "zh-CN"
    assert payload["policy"]["skillDraftMustBeChinese"] is True


class FakeMemoryFile:
    scope = "user"
    owner_id = "default"
    file_path = "/tmp/user_memory.md"
