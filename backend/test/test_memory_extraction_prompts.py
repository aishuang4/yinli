"""记忆提炼提示词测试。"""

from __future__ import annotations

import json

from src.memory import extraction_agents


def test_memory_reflection_prompt_requires_chinese_output():
    prompt = extraction_agents._build_memory_reflection_system_prompt("user")

    assert "必须使用中文记录" in prompt
    assert "所有提取出的信息都必须以中文记录" in prompt
    assert "原始对话是英文" in prompt
    assert "不要把英文原句直接作为记忆内容保存" in prompt
    assert "英文技术名词" in prompt
    assert "代码标识符" in prompt
    assert "note" in prompt
    assert "reason" in prompt


def test_memory_reflection_prompts_separate_scope_focus():
    user_prompt = extraction_agents._build_memory_reflection_system_prompt("user")
    agent_prompt = extraction_agents._build_memory_reflection_system_prompt("agent")
    global_prompt = extraction_agents._build_memory_reflection_system_prompt("global_preference")

    assert "用户本人长期稳定的偏好" in user_prompt
    assert "不要记录：Agent 的解决方案" in user_prompt
    assert "这个具体 Agent 下次遇到类似工作时" in agent_prompt
    assert "已验证的解决方案、排查路径、实现策略" in agent_prompt
    assert "不要记录：用户个人喜好" in agent_prompt
    assert "所有 Agent、Team 和整个工作台" in global_prompt
    assert "跨 Agent 共享的架构约束" in global_prompt
    assert "不要记录：单个 Agent 的实现经验" in global_prompt
    assert "只写入最精确的一层" in user_prompt
    assert "只写入最精确的一层" in agent_prompt
    assert "只写入最精确的一层" in global_prompt


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
    assert payload["policy"]["allExtractedInformationMustBeChinese"] is True
    assert payload["policy"]["translateSourceFactsToChinese"] is True
    assert payload["policy"]["notesAndReasonsMustBeChinese"] is True
    assert payload["policy"]["preserveEnglishOnlyForTechnicalIdentifiers"] is True
    assert payload["policy"]["scopeBoundary"]["scopeFocus"].startswith("记录用户本人")


def test_memory_reflection_user_prompt_sets_scope_boundary():
    agent_payload = json.loads(
        extraction_agents._build_memory_reflection_user_prompt(
            user_message="你以后遇到类似问题先看运行日志。",
            assistant_text="好的。",
            agent_id="agent-1",
            agent_name="Agent",
            memory_file=FakeMemoryFile(scope="agent"),
        ),
    )
    global_payload = json.loads(
        extraction_agents._build_memory_reflection_user_prompt(
            user_message="整个工作台都不要恢复旧 Runtime。",
            assistant_text="好的。",
            agent_id="agent-1",
            agent_name="Agent",
            memory_file=FakeMemoryFile(scope="global_preference"),
        ),
    )

    assert "当前 Agent 的可复用工作方法" in agent_payload["policy"]["scopeBoundary"]["scopeFocus"]
    assert "纯用户个人偏好" in agent_payload["policy"]["scopeBoundary"]["mustSkip"]
    assert "所有 Agent、Team 和整个工作台共享" in global_payload["policy"]["scopeBoundary"]["scopeFocus"]
    assert "单个 Agent 的解决方法" in global_payload["policy"]["scopeBoundary"]["mustSkip"]



def test_skill_evolution_prompt_requires_chinese_draft():
    prompt = extraction_agents._build_skill_evolution_system_prompt()

    assert "所有 Skill 草稿字段必须使用中文记录" in prompt
    assert "可复用经验、触发条件、步骤、约束和输出形态翻译成中文" in prompt
    assert "不要把英文原句直接作为 Skill 内容保存" in prompt
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
    assert payload["policy"]["allExtractedInformationMustBeChinese"] is True
    assert payload["policy"]["translateReusableKnowledgeToChinese"] is True
    assert payload["policy"]["skillDraftMustBeChinese"] is True
    assert payload["policy"]["preserveEnglishOnlyForTechnicalIdentifiers"] is True


class FakeMemoryFile:
    owner_id = "default"
    file_path = "/tmp/user_memory.md"

    def __init__(self, scope: str = "user") -> None:
        self.scope = scope
