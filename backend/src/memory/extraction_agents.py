"""记忆与 Skill 提炼专用系统 Agent。

这些 Agent 是后台基础设施：无默认工具、无 MCP、无 Skills，只输出结构化 JSON。
"""

from __future__ import annotations

import json
import re
from hashlib import sha256
from typing import Any, Literal

from agentscope.message import UserMsg
from agentscope.permission import PermissionMode
from pydantic import BaseModel, Field

from src.agents.runtime_types import AgentGuardrails, AgentModelOptions, RuntimeToolScope
from src.tools.memory_reflection import MemoryFileToolSet, SkillDraftPropose

MemoryScope = Literal["user", "agent", "global_preference"]
MemorySection = Literal[
    "Stable Preferences",
    "Stable Behaviors",
    "Profile",
    "Workspace Rules",
    "Corrections",
    "Negative Rules",
]
MemoryOperation = Literal["append", "update", "delete", "skip"]

MEMORY_REFLECTION_PROMPT_VERSION = "2026-07-03-v2"
SKILL_EVOLUTION_PROMPT_VERSION = "2026-07-03-v2"


class MemoryExtractionItem(BaseModel):
    """MemoryReflectionAgent 返回的单条 Markdown 更新。"""

    operation: MemoryOperation = "append"
    scope: MemoryScope
    section: MemorySection = "Stable Preferences"
    note: str = Field(default="", max_length=600)
    exact_text: str = Field(default="", alias="exactText", max_length=600)
    replacement_text: str = Field(default="", alias="replacementText", max_length=600)
    confidence: float = Field(default=0.5, ge=0, le=1)
    reason: str = Field(default="", max_length=300)

    model_config = {"populate_by_name": True, "extra": "ignore"}


class MemoryReflectionAgentPayload(BaseModel):
    """MemoryReflectionAgent 的结构化输出。"""

    should_write: bool = Field(default=False, alias="shouldWrite")
    reason: str = Field(default="", max_length=400)
    memories: list[MemoryExtractionItem] = Field(default_factory=list)

    model_config = {"populate_by_name": True, "extra": "ignore"}


class SkillDraftPayload(BaseModel):
    """SkillEvolutionAgent 输出的标准 Skill 草稿。"""

    name: str = Field(default="", max_length=80)
    description: str = Field(default="", max_length=300)
    trigger: str = Field(default="", max_length=300)
    content: str = Field(default="", max_length=8000)
    tags: list[str] = Field(default_factory=list)


class SkillEvolutionAgentPayload(BaseModel):
    """SkillEvolutionAgent 的结构化输出。"""

    reusable: bool = False
    confidence: float = Field(default=0.0, ge=0, le=1)
    reason: str = Field(default="", max_length=500)
    draft: SkillDraftPayload | None = None

    model_config = {"extra": "ignore"}


async def run_memory_reflection_agent(
    *,
    user_message: str,
    assistant_text: str,
    agent_id: str,
    agent_name: str,
    memory_file: MemoryFileToolSet,
    model_name: str,
    fallback_names: list[str] | None = None,
    invocation_key: str = "",
) -> MemoryReflectionAgentPayload | None:
    """调用指定 scope 的 MemoryReflectionAgent 提炼 Markdown 记忆候选。"""

    kind, name = _memory_agent_identity(memory_file.scope)
    text = await _run_json_agent(
        agent_id=_system_agent_id(
            kind,
            model_name,
            fallback_names or [],
            f"{MEMORY_REFLECTION_PROMPT_VERSION}:{memory_file.scope}:{memory_file.owner_id}:{invocation_key}",
        ),
        name=name,
        system_prompt=_build_memory_reflection_system_prompt(memory_file.scope),
        user_prompt=_build_memory_reflection_user_prompt(
            user_message=user_message,
            assistant_text=assistant_text,
            agent_id=agent_id,
            agent_name=agent_name,
            memory_file=memory_file,
        ),
        model_name=model_name,
        fallback_names=fallback_names or [],
        max_tokens=1600,
        extra_tools=memory_file.tools(),
    )
    if memory_file.operations:
        return MemoryReflectionAgentPayload.model_validate({
            "shouldWrite": True,
            "reason": "MemoryReflectionAgent updated the bound memory file with memory tools.",
            "memories": memory_file.operations,
        })
    data = _extract_json_object(text)
    if not data:
        return None
    return MemoryReflectionAgentPayload.model_validate(data)


async def run_skill_evolution_agent(
    *,
    user_message: str,
    assistant_text: str,
    agent_id: str,
    agent_name: str,
    model_name: str,
    fallback_names: list[str] | None = None,
    invocation_key: str = "",
) -> SkillEvolutionAgentPayload | None:
    """调用 SkillEvolutionAgent 判断本次工作是否值得沉淀为标准 Skill 草稿。"""

    draft_tool = SkillDraftPropose()
    text = await _run_json_agent(
        agent_id=_system_agent_id(
            "skill-evolution",
            model_name,
            fallback_names or [],
            f"{SKILL_EVOLUTION_PROMPT_VERSION}:{invocation_key}",
        ),
        name="SkillEvolutionAgent",
        system_prompt=_build_skill_evolution_system_prompt(),
        user_prompt=_build_skill_evolution_user_prompt(
            user_message=user_message,
            assistant_text=assistant_text,
            agent_id=agent_id,
            agent_name=agent_name,
        ),
        model_name=model_name,
        fallback_names=fallback_names or [],
        max_tokens=2200,
        extra_tools=[draft_tool],
    )
    if draft_tool.drafts:
        return SkillEvolutionAgentPayload.model_validate(draft_tool.drafts[-1])
    data = _extract_json_object(text)
    if not data:
        return None
    return SkillEvolutionAgentPayload.model_validate(data)


async def _run_json_agent(
    *,
    agent_id: str,
    name: str,
    system_prompt: str,
    user_prompt: str,
    model_name: str,
    fallback_names: list[str],
    max_tokens: int,
    extra_tools: list[Any] | None = None,
) -> str:
    from src.agents.factory import get_or_create_agent

    agent = get_or_create_agent(
        agent_id=agent_id,
        name=name,
        system_prompt=system_prompt,
        model_name=model_name,
        fallback_names=fallback_names,
        thinking_enable=False,
        permission_mode=PermissionMode.BYPASS,
        model_options=AgentModelOptions(max_tokens=max_tokens, max_react_iters=2, temperature=0.1),
        guardrails=AgentGuardrails(
            network_enabled=False,
            tool_permission_profile="read_only",
        ),
        tool_scope=RuntimeToolScope(
            allow_mcp=False,
            allow_knowledge=False,
            allow_bash=False,
            allow_http=False,
        ),
        include_default_tools=False,
        extra_tools=extra_tools or [],
    )
    text = ""
    async for event in agent.reply_stream(UserMsg(name="user", content=user_prompt)):
        event_data = event.model_dump(mode="json")
        if event_data.get("type") == "TEXT_BLOCK_DELTA":
            text += str(event_data.get("delta") or "")
    return text


def _system_agent_id(kind: str, model_name: str, fallback_names: list[str], prompt_version: str) -> str:
    fallback_key = ",".join(fallback_names) if fallback_names else "none"
    raw = f"{kind}:{model_name}:fallbacks-{fallback_key}:prompt-{prompt_version}"
    return f"system:{kind}:{sha256(raw.encode('utf-8')).hexdigest()[:16]}"


def _memory_agent_identity(scope: str) -> tuple[str, str]:
    if scope == "agent":
        return "agent-memory-reflection", "AgentMemoryReflectionAgent"
    if scope == "global_preference":
        return "global-memory-reflection", "GlobalMemoryReflectionAgent"
    return "user-memory-reflection", "UserMemoryReflectionAgent"


def _scope_policy(scope: str) -> str:
    if scope == "agent":
        return "只编辑 agent memory：记录这个具体 Agent 的行为规则、纠正、工作方式、persona 和能力边界。"
    if scope == "global_preference":
        return "只编辑 global preference memory：记录对多个 Agent、Team 或项目都有效的工作台全局规则。"
    return "只编辑 user memory：记录用户稳定偏好、用户画像、沟通习惯和跨 Agent 有用的用户事实。"


def _build_memory_reflection_system_prompt(scope: str) -> str:
    _, name = _memory_agent_identity(scope)
    return "\n".join([
        f"你是 {name}，一个后台基础设施 Agent。",
        "只返回一个紧凑、合法的 JSON 对象。不要写 markdown fence。",
        "你正在编辑且只能编辑一个已绑定的 Markdown 记忆文件。",
        _scope_policy(scope),
        "所有新增、修改后的记忆内容、note、reason、摘要和 Skill 草稿都必须使用中文记录。",
        "英文技术名词、代码标识符、文件名、API 名称、环境变量可以保留原文，但解释性文本必须是中文。",
        "只提取本轮已完成对话中长期稳定、未来有用的信息。",
        "禁止记录 secrets、tokens、passwords、private keys、cookies、一次性验证码或大段复制内容。",
        "不要记录工具调用过程、observation、chain-of-thought 或实现噪音。",
        "修改记忆前必须先调用 memory_read 查看当前文件内容。",
        "使用 memory_add 添加新记忆，使用 memory_update 精确替换已有条目，使用 memory_delete 删除过期条目。",
        "这些 memory tools 已绑定唯一文件路径；禁止要求、猜测或编造其他路径。",
        "如果本轮信息是临时的、重复的、低置信度的，或与当前绑定 scope 无关，不要编辑。",
        "记忆必须对未来运行有帮助，并且不能与用户最新指令冲突。",
        f"这个 Agent 唯一允许的 scope 是：{scope}。",
        "可用章节：Stable Preferences, Stable Behaviors, Profile, Workspace Rules, Corrections, Negative Rules。",
        "如果已经成功使用 memory tools，工具调用后只返回一个最小 JSON 摘要。",
        "Schema:",
        json.dumps(
            {
                "shouldWrite": True,
                "reason": "简短中文理由",
                "memories": [
                    {
                        "scope": "user | agent | global_preference",
                        "section": "Stable Preferences | Stable Behaviors | Profile | Workspace Rules | Corrections | Negative Rules",
                        "operation": "append | update | delete | skip",
                        "note": "一条简短中文记忆",
                        "exactText": "需要修改或删除的原文，如适用",
                        "replacementText": "中文替换文本，如适用",
                        "confidence": 0.0,
                        "reason": "为什么这是长期稳定记忆",
                    },
                ],
            },
            ensure_ascii=False,
        ),
    ])


def _build_memory_reflection_user_prompt(
    *,
    user_message: str,
    assistant_text: str,
    agent_id: str,
    agent_name: str,
    memory_file: MemoryFileToolSet,
) -> str:
    payload = {
        "agent": {"id": agent_id, "name": agent_name},
        "boundMemoryFile": {
            "scope": memory_file.scope,
            "ownerId": memory_file.owner_id,
            "filePath": memory_file.file_path,
        },
        "userMessage": user_message,
        "assistantFinalOutput": assistant_text,
        "policy": {
            "markdownOnly": True,
            "maxOperations": 4,
            "skipIfTemporary": True,
            "skipIfSensitive": True,
            "mustUseBoundMemoryToolsForWrite": True,
            "outputLanguage": "zh-CN",
            "notesAndReasonsMustBeChinese": True,
        },
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _build_skill_evolution_system_prompt() -> str:
    return "\n".join([
        "你是 SkillEvolutionAgent，一个后台基础设施 Agent。",
        "只返回一个紧凑、合法的 JSON 对象。不要写 markdown fence。",
        "判断本次已完成工作是否应该沉淀为可复用的标准 Skill。",
        "所有 Skill 草稿字段必须使用中文记录：name、description、trigger、content、reason 都必须是中文。",
        "英文技术名词、代码标识符、文件名、API 名称可以保留原文，但说明性文字必须是中文。",
        "可复用 Skill 必须有可重复目标、触发条件、步骤、约束和输出形态。",
        "拒绝一次性闲聊、私有客户数据、密钥、临时路径、未完成工作，以及无法泛化的项目细节。",
        "输出只能是 Skill 草稿，不要绑定到任何 Agent。",
        "优先调用 skill_draft_propose 提交候选；该工具不会注册 Skill，也不会绑定 Agent。",
        "Schema:",
        json.dumps(
            {
                "reusable": True,
                "confidence": 0.0,
                "reason": "简短中文理由",
                "draft": {
                    "name": "简短中文 Skill 名称",
                    "description": "这个 Skill 做什么",
                    "trigger": "什么时候使用这个 Skill",
                    "content": "中文 SKILL.md 风格操作说明",
                    "tags": ["memory", "hooks"],
                },
            },
            ensure_ascii=False,
        ),
    ])


def _build_skill_evolution_user_prompt(
    *,
    user_message: str,
    assistant_text: str,
    agent_id: str,
    agent_name: str,
) -> str:
    payload = {
        "agent": {"id": agent_id, "name": agent_name},
        "userMessage": user_message,
        "assistantFinalOutput": assistant_text,
        "policy": {
            "draftOnly": True,
            "doNotRegister": True,
            "doNotBindToAgent": True,
            "maxContentChars": 8000,
            "outputLanguage": "zh-CN",
            "skillDraftMustBeChinese": True,
        },
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _extract_json_object(text: str) -> dict[str, Any]:
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(raw[start : end + 1])
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}
