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
from src.tools.memory_reflection import MemoryContextRead, MemoryOperationPropose, SkillDraftPropose

MemoryScope = Literal["user", "agent", "global_preference"]
MemorySection = Literal[
    "Stable Preferences",
    "Stable Behaviors",
    "Profile",
    "Workspace Rules",
    "Corrections",
    "Negative Rules",
]
MemoryOperation = Literal["append", "skip"]

MEMORY_REFLECTION_PROMPT_VERSION = "2026-07-03-v1"
SKILL_EVOLUTION_PROMPT_VERSION = "2026-07-03-v1"


class MemoryExtractionItem(BaseModel):
    """MemoryReflectionAgent 返回的单条 Markdown 更新。"""

    operation: MemoryOperation = "append"
    scope: MemoryScope
    section: MemorySection
    note: str = Field(default="", max_length=600)
    confidence: float = Field(default=0.5, ge=0, le=1)
    reason: str = Field(default="", max_length=300)


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
    existing_memory_context: str = "",
    model_name: str,
    fallback_names: list[str] | None = None,
) -> MemoryReflectionAgentPayload | None:
    """调用 MemoryReflectionAgent 提炼 Markdown 记忆候选。"""

    operation_tool = MemoryOperationPropose()
    text = await _run_json_agent(
        agent_id=_system_agent_id("memory-reflection", model_name, fallback_names or [], MEMORY_REFLECTION_PROMPT_VERSION),
        name="MemoryReflectionAgent",
        system_prompt=_build_memory_reflection_system_prompt(),
        user_prompt=_build_memory_reflection_user_prompt(
            user_message=user_message,
            assistant_text=assistant_text,
            agent_id=agent_id,
            agent_name=agent_name,
            existing_memory_context=existing_memory_context,
        ),
        model_name=model_name,
        fallback_names=fallback_names or [],
        max_tokens=1600,
        extra_tools=[
            MemoryContextRead(existing_memory_context),
            operation_tool,
        ],
    )
    if operation_tool.operations:
        return MemoryReflectionAgentPayload.model_validate({
            "shouldWrite": True,
            "reason": "MemoryReflectionAgent submitted operations through memory_operation_propose.",
            "memories": operation_tool.operations,
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
) -> SkillEvolutionAgentPayload | None:
    """调用 SkillEvolutionAgent 判断本次工作是否值得沉淀为标准 Skill 草稿。"""

    draft_tool = SkillDraftPropose()
    text = await _run_json_agent(
        agent_id=_system_agent_id("skill-evolution", model_name, fallback_names or [], SKILL_EVOLUTION_PROMPT_VERSION),
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


def _build_memory_reflection_system_prompt() -> str:
    return "\n".join([
        "You are MemoryReflectionAgent, a background infrastructure agent.",
        "Return only one compact valid JSON object. Do not call tools. Do not write markdown fences.",
        "Extract only durable, useful memory from the completed turn.",
        "Never store secrets, tokens, passwords, private keys, cookies, one-time codes, or large copied content.",
        "Do not store tool calls, observations, chain-of-thought, or implementation noise.",
        "Use memory_context_read when you need current memory for dedupe and conflict checks.",
        "Use memory_operation_propose to submit each memory operation candidate.",
        "The tool does not write files; the backend validates and applies accepted candidates.",
        "A memory must be useful in future runs and must not conflict with the user's latest instruction.",
        "Scopes:",
        "- user: stable user preference/profile useful across agents.",
        "- agent: behavior, correction, or boundary for this specific agent.",
        "- global_preference: workspace-wide rule useful across agents or teams.",
        "Sections: Stable Preferences, Stable Behaviors, Profile, Workspace Rules, Corrections, Negative Rules.",
        "Schema:",
        json.dumps(
            {
                "shouldWrite": True,
                "reason": "short Chinese reason",
                "memories": [
                    {
                        "scope": "user | agent | global_preference",
                        "section": "Stable Preferences | Stable Behaviors | Profile | Workspace Rules | Corrections | Negative Rules",
                        "operation": "append | skip",
                        "note": "one concise Chinese memory sentence",
                        "confidence": 0.0,
                        "reason": "why this is durable",
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
    existing_memory_context: str,
) -> str:
    payload = {
        "agent": {"id": agent_id, "name": agent_name},
        "existingMemoryContext": existing_memory_context,
        "userMessage": user_message,
        "assistantFinalOutput": assistant_text,
        "policy": {
            "markdownOnly": True,
            "maxMemories": 4,
            "skipIfTemporary": True,
            "skipIfSensitive": True,
        },
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _build_skill_evolution_system_prompt() -> str:
    return "\n".join([
        "You are SkillEvolutionAgent, a background infrastructure agent.",
        "Return only one compact valid JSON object. Do not call tools. Do not write markdown fences.",
        "Decide whether the completed work should become a reusable standard Skill.",
        "A reusable Skill needs a repeatable goal, trigger, steps, constraints, and output shape.",
        "Reject one-off chat, private customer data, secrets, temporary paths, incomplete work, and project-specific details that cannot generalize.",
        "The output is a Skill draft only. Do not bind it to any agent.",
        "Prefer calling skill_draft_propose to submit the candidate; the tool does not register or bind the Skill.",
        "Schema:",
        json.dumps(
            {
                "reusable": True,
                "confidence": 0.0,
                "reason": "short Chinese reason",
                "draft": {
                    "name": "short Skill name",
                    "description": "what this Skill does",
                    "trigger": "when to use this Skill",
                    "content": "SKILL.md style concise instructions",
                    "tags": ["memory", "example"],
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
