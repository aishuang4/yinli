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
from src.memory.skill_evolution import SkillEvolutionGateDecision, WorkflowEvidence
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
SkillGranularity = Literal["too_small", "atomic", "too_broad"]

MEMORY_REFLECTION_PROMPT_VERSION = "2026-07-03-v4"
SKILL_EVOLUTION_PROMPT_VERSION = "2026-07-06-v1"


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


class SkillSplitSuggestion(BaseModel):
    """过大工作流的原子 Skill 拆分建议。"""

    name: str = Field(default="", max_length=80)
    trigger: str = Field(default="", max_length=300)
    reason: str = Field(default="", max_length=300)


class SkillVariableFlow(BaseModel):
    """Skill 草稿中必须保留的变量传递关系。"""

    source: str = ""
    target: str = ""
    variable: str = ""


class SkillEvolutionAgentPayload(BaseModel):
    """SkillEvolutionAgent 的结构化输出。"""

    reusable: bool = False
    confidence: float = Field(default=0.0, ge=0, le=1)
    granularity: SkillGranularity = "atomic"
    requires_workflow: bool = Field(default=False, alias="requiresWorkflow")
    reason: str = Field(default="", max_length=500)
    suggested_split: list[SkillSplitSuggestion] = Field(default_factory=list, alias="suggestedSplit")
    variable_flow: list[SkillVariableFlow] = Field(default_factory=list, alias="variableFlow")
    draft: SkillDraftPayload | None = None

    model_config = {"populate_by_name": True, "extra": "ignore"}


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
    workflow_evidence: WorkflowEvidence | None = None,
    router_gate: SkillEvolutionGateDecision | None = None,
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
            workflow_evidence=workflow_evidence,
            router_gate=router_gate,
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
        return "只编辑 agent memory：记录这个具体 Agent 在协作过程中的可复用工作方法、解决方案、反面案例、纠正和行为边界。"
    if scope == "global_preference":
        return "只编辑 global preference memory：记录所有 Agent、Team 或整个工作台都必须共享的规则、约束和项目级约定。"
    return "只编辑 user memory：记录用户本人长期稳定的偏好、画像、沟通习惯和跨 Agent 有用的用户事实。"


def _scope_focus_lines(scope: str) -> list[str]:
    if scope == "agent":
        return [
            "agent memory 的核心问题是：这个具体 Agent 下次遇到类似工作时，应该复用什么方法，避免什么错误？",
            "应该记录：已验证的解决方案、排查路径、实现策略、协作流程、失败原因、反面案例、用户对该 Agent 工作方式的纠正。",
            "可以记录：这个 Agent 应长期保持或避免的行为方式，例如默认先读代码、不要恢复某个已删除 Runtime、遇到某类问题先检查某个文件。",
            "不要记录：用户个人喜好，除非它是用户专门要求这个 Agent 遵守的工作方式；不要记录全局架构规则，除非只适用于当前 Agent。",
            "优先章节：Stable Behaviors、Corrections、Negative Rules。解决方法和方案放入 Stable Behaviors；反面案例和禁止事项放入 Negative Rules；用户纠正放入 Corrections。",
        ]
    if scope == "global_preference":
        return [
            "global preference memory 的核心问题是：这条信息是否所有 Agent、Team 和整个工作台都必须共同遵守？",
            "应该记录：跨 Agent 共享的架构约束、项目级规则、工作台默认策略、所有 Agent 都要遵守的禁止事项、团队协作约定。",
            "可以记录：用户明确说“全局、整个工作台、所有 Agent、所有智能体、项目约定、团队约定”时给出的长期规则。",
            "不要记录：单个 Agent 的实现经验、某次具体修复过程、用户个人表达偏好，除非用户明确要求它成为所有 Agent 的共享规则。",
            "优先章节：Workspace Rules、Negative Rules、Stable Preferences。项目和架构约束放入 Workspace Rules；全局禁止事项放入 Negative Rules。",
        ]
    return [
        "user memory 的核心问题是：未来任何 Agent 与这个用户协作时，理解用户本人需要知道什么？",
        "应该记录：用户的长期偏好、沟通风格、输出格式偏好、语言偏好、个人工作习惯、稳定背景信息和反复表达的个人要求。",
        "可以记录：用户希望以后默认怎样回答、如何解释、是否简洁、是否需要中文、用户对交互方式的偏好。",
        "不要记录：Agent 的解决方案、代码实现过程、项目架构规则、工作台全局约束，除非它本质上是用户个人偏好。",
        "优先章节：Stable Preferences、Profile、Corrections、Negative Rules。个人偏好放入 Stable Preferences；用户画像放入 Profile；用户纠正交互方式放入 Corrections。",
    ]


def _scope_policy_payload(scope: str) -> dict[str, object]:
    if scope == "agent":
        return {
            "scopeFocus": "记录当前 Agent 的可复用工作方法、解决方案、反面案例、纠正和行为边界。",
            "shouldWrite": [
                "当前 Agent 下次可复用的解决方法或方案",
                "当前 Agent 应避免的错误路径或反面案例",
                "用户对当前 Agent 工作方式的纠正",
            ],
            "mustSkip": [
                "纯用户个人偏好",
                "所有 Agent 都要共享的全局规则",
                "只描述本次过程但未来不可复用的信息",
            ],
            "preferredSections": ["Stable Behaviors", "Corrections", "Negative Rules"],
        }
    if scope == "global_preference":
        return {
            "scopeFocus": "记录所有 Agent、Team 和整个工作台共享的长期规则、约束和项目级约定。",
            "shouldWrite": [
                "明确适用于整个工作台或所有 Agent 的规则",
                "跨 Agent 共享的架构约束",
                "项目级或团队级长期约定",
            ],
            "mustSkip": [
                "单个 Agent 的解决方法",
                "用户个人沟通偏好",
                "某次具体任务的局部实现细节",
            ],
            "preferredSections": ["Workspace Rules", "Negative Rules", "Stable Preferences"],
        }
    return {
        "scopeFocus": "记录用户本人的长期偏好、画像、沟通习惯和跨 Agent 有用的个人事实。",
        "shouldWrite": [
            "用户稳定的语言、格式、详细程度和沟通偏好",
            "用户个人工作习惯或背景信息",
            "用户对未来交互方式的明确要求",
        ],
        "mustSkip": [
            "Agent 的解决方案和反面案例",
            "项目架构或工作台全局规则",
            "只对当前任务有效的临时要求",
        ],
        "preferredSections": ["Stable Preferences", "Profile", "Corrections", "Negative Rules"],
    }


def _build_memory_reflection_system_prompt(scope: str) -> str:
    _, name = _memory_agent_identity(scope)
    return "\n".join([
        f"你是 {name}，一个后台基础设施 Agent。",
        "只返回一个紧凑、合法的 JSON 对象。不要写 markdown fence。",
        "你正在编辑且只能编辑一个已绑定的 Markdown 记忆文件。",
        _scope_policy(scope),
        *_scope_focus_lines(scope),
        "所有提取出的信息都必须以中文记录，必须使用中文记录 shouldWrite reason、memories.reason、note、replacementText、工具参数、工具操作摘要和后续 Skill 草稿。",
        "即使原始对话是英文，也要把可沉淀的事实、规则、偏好和修正翻译成中文后写入记忆。",
        "英文技术名词、代码标识符、文件名、API 名称、环境变量、命令和专有产品名可以保留原文，但解释性文本必须是中文。",
        "不要把英文原句直接作为记忆内容保存，除非它本身是必须精确保留的代码、命令、路径、配置项或专有名词。",
        "只提取本轮已完成对话中长期稳定、未来有用的信息。",
        "禁止记录 secrets、tokens、passwords、private keys、cookies、一次性验证码或大段复制内容。",
        "不要记录工具调用过程、observation、chain-of-thought 或实现噪音。",
        "修改记忆前必须先调用 memory_read 查看当前文件内容。",
        "使用 memory_add 添加新记忆，使用 memory_update 精确替换已有条目，使用 memory_delete 删除过期条目。",
        "这些 memory tools 已绑定唯一文件路径；禁止要求、猜测或编造其他路径。",
        "如果本轮信息是临时的、重复的、低置信度的，或与当前绑定 scope 无关，不要编辑。",
        "如果信息同时可能属于多层记忆，只写入最精确的一层；不要为了凑数把同一条内容写入多个 scope。",
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
            "allExtractedInformationMustBeChinese": True,
            "translateSourceFactsToChinese": True,
            "notesAndReasonsMustBeChinese": True,
            "preserveEnglishOnlyForTechnicalIdentifiers": True,
            "scopeBoundary": _scope_policy_payload(memory_file.scope),
        },
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _build_skill_evolution_system_prompt() -> str:
    return "\n".join([
        "你是 SkillEvolutionAgent，一个后台基础设施 Agent。",
        "只返回一个紧凑、合法的 JSON 对象。不要写 markdown fence。",
        "判断本次已完成工作是否应该沉淀为可复用、原子化的标准 Skill。",
        "你只能在 routerGate.shouldEvaluate=true 时评估 Skill；如果 gate 不通过，必须 reusable=false。",
        "workflowEvidence 是唯一可信的过程证据来源，不能编造未出现在 evidence 中的工具链、测试或文件。",
        "所有 Skill 草稿字段必须使用中文记录：name、description、trigger、content、reason 都必须是中文。",
        "即使原始对话是英文，也要把可复用经验、触发条件、步骤、约束和输出形态翻译成中文后写入草稿。",
        "英文技术名词、代码标识符、文件名、API 名称、命令和专有产品名可以保留原文，但说明性文字必须是中文。",
        "不要把英文原句直接作为 Skill 内容保存，除非它本身是必须精确保留的代码、命令、路径、配置项或专有名词。",
        "可复用 Skill 必须有可重复目标、单一触发条件、步骤、约束和输出形态。",
        "必须判断 granularity：too_small 表示太小不生成；atomic 表示原子能力可生成；too_broad 表示过大流程不能生成单个 Skill。",
        "只有 granularity=atomic 且 reusable=true 时才允许生成 draft。",
        "如果 granularity=too_broad，必须 reusable=false、requiresWorkflow=true、draft=null，并给出 suggestedSplit 原子 Skill 拆分建议。",
        "禁止生成 do_everything_* 这类超级 Skill；跨阶段流程应该由 Workflow 编排器组合原子 Skill。",
        "Skill 步骤必须保留变量流：工具返回值要写成变量传递，禁止硬编码本轮具体 ID、路径、URL、token 或运行时返回值。",
        "successfulToolChain 只能作为主步骤来源；usefulFailures 只能转写为注意事项或反面案例；discardedFailures 不得进入 Skill 内容。",
        "拒绝一次性闲聊、私有客户数据、密钥、临时路径、未完成工作，以及无法泛化的项目细节。",
        "输出只能是 Skill 草稿，不要绑定到任何 Agent。",
        "优先调用 skill_draft_propose 提交候选；该工具不会注册 Skill，也不会绑定 Agent。",
        "Schema:",
        json.dumps(
            {
                "reusable": True,
                "confidence": 0.0,
                "granularity": "atomic",
                "requiresWorkflow": False,
                "reason": "简短中文理由",
                "suggestedSplit": [
                    {"name": "原子 Skill 名称", "trigger": "触发条件", "reason": "为什么应该拆出它"},
                ],
                "variableFlow": [
                    {"source": "search_user.user_id", "target": "send_email.user_id", "variable": "user_id"},
                ],
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
    workflow_evidence: WorkflowEvidence | None = None,
    router_gate: SkillEvolutionGateDecision | None = None,
) -> str:
    payload = {
        "agent": {"id": agent_id, "name": agent_name},
        "userMessage": user_message,
        "assistantFinalOutput": assistant_text,
        "routerGate": (
            router_gate.model_dump(mode="json", by_alias=True)
            if router_gate
            else {"shouldEvaluate": True, "reason": "未提供 gate，兼容旧调用。", "risk": "medium", "signals": []}
        ),
        "workflowEvidence": (
            workflow_evidence.model_dump(mode="json", by_alias=True)
            if workflow_evidence
            else {}
        ),
        "policy": {
            "draftOnly": True,
            "doNotRegister": True,
            "doNotBindToAgent": True,
            "maxContentChars": 8000,
            "outputLanguage": "zh-CN",
            "allExtractedInformationMustBeChinese": True,
            "translateReusableKnowledgeToChinese": True,
            "skillDraftMustBeChinese": True,
            "preserveEnglishOnlyForTechnicalIdentifiers": True,
            "requireAtomicGranularity": True,
            "rejectTooBroadSkill": True,
            "preserveVariableFlow": True,
            "doNotHardcodeRuntimeValues": True,
            "successfulToolsBecomeSteps": True,
            "usefulFailuresBecomePitfallsOnly": True,
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
