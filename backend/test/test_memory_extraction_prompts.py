"""记忆提炼提示词测试。"""

from __future__ import annotations

import json

from src.memory import extraction_agents


def test_memory_reflection_prompt_requires_chinese_output():
    prompt = extraction_agents._build_memory_reflection_system_prompt()

    assert "所有提取出的信息都必须以中文记录" in prompt
    assert "英文技术名词" in prompt
    assert "代码标识符" in prompt
    assert "note" in prompt
    assert "reason" in prompt


def test_memory_reflection_prompt_covers_all_scopes_and_tool_trajectory():
    prompt = extraction_agents._build_memory_reflection_system_prompt()

    # 一路 Agent 同时负责三层，各层边界都在同一份 system prompt 里
    assert "用户本人长期稳定的偏好" in prompt
    assert "不要记录：Agent 的解决方案" in prompt
    assert "这个具体 Agent 下次遇到类似工作时" in prompt
    assert "已验证的解决方案、排查路径、实现策略" in prompt
    assert "所有 Agent、Team 和整个工作台" in prompt
    assert "不要记录：单个 Agent 的实现经验" in prompt
    assert "只写入最精确的一层" in prompt
    assert "禁止把同一条内容写进多个 scope" in prompt
    # 工具轨迹用于 agent 层，且不能逐条抄
    assert "toolTrajectory" in prompt
    assert "不要把轨迹逐条抄进记忆" in prompt
    assert "你没有任何工具" in prompt
    assert "exactText" in prompt


def test_memory_reflection_user_prompt_sets_chinese_policy_and_files():
    payload = json.loads(
        extraction_agents._build_memory_reflection_user_prompt(
            user_message="以后默认中文。",
            assistant_text="好的。",
            agent_id="agent-1",
            agent_name="Agent",
            memory_files={
                "user": FakeMemoryFile(),
                "agent": FakeMemoryFile(scope="agent"),
                "global_preference": FakeMemoryFile(scope="global_preference"),
            },
            tool_trajectory=[{"tool": "Read", "status": "completed", "args": "{}"}],
        ),
    )

    assert payload["policy"]["outputLanguage"] == "zh-CN"
    assert payload["policy"]["allExtractedInformationMustBeChinese"] is True
    assert payload["policy"]["preserveEnglishOnlyForTechnicalIdentifiers"] is True
    assert payload["policy"]["oneScopePerFact"] is True
    assert payload["toolTrajectory"][0]["tool"] == "Read"
    files = payload["memoryFiles"]
    assert set(files) == {"user", "agent", "global_preference"}
    assert files["user"]["content"] == "# Summary\n"
    assert files["user"]["scopeBoundary"]["scopeFocus"].startswith("记录用户本人")
    assert "当前 Agent 的可复用工作方法" in files["agent"]["scopeBoundary"]["scopeFocus"]
    assert "纯用户个人偏好" in files["agent"]["scopeBoundary"]["mustSkip"]
    assert "所有 Agent、Team 和整个工作台共享" in files["global_preference"]["scopeBoundary"]["scopeFocus"]
    assert "单个 Agent 的解决方法" in files["global_preference"]["scopeBoundary"]["mustSkip"]


def test_memory_reflection_user_prompt_truncates_large_files():
    big = FakeMemoryFile()
    big.content = "x" * (extraction_agents.MEMORY_FILE_PROMPT_CHARS + 500)
    payload = json.loads(
        extraction_agents._build_memory_reflection_user_prompt(
            user_message="以后默认中文。",
            assistant_text="好的。",
            agent_id="agent-1",
            agent_name="Agent",
            memory_files={"user": big},
            tool_trajectory=[],
        ),
    )
    content = payload["memoryFiles"]["user"]["content"]
    assert content.endswith("…(已截断)")
    assert len(content) < extraction_agents.MEMORY_FILE_PROMPT_CHARS + 50


class FakeMemoryFile:
    owner_id = "default"
    file_path = "/tmp/user_memory.md"

    def __init__(self, scope: str = "user") -> None:
        self.scope = scope
        self.content = "# Summary\n"

    def read_content(self) -> str:
        return self.content
