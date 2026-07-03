"""记忆提炼专用内部工具。

这些工具只给系统基础设施 Agent 使用，用于读取摘要级记忆上下文、提交记忆操作候选和
提交 Skill 草稿候选。工具本身不直接修改长期记忆或注册 Skill。
"""

from __future__ import annotations

import json
from typing import Any

from agentscope.message import TextBlock, ToolResultState
from agentscope.permission import PermissionBehavior, PermissionContext, PermissionDecision
from agentscope.tool import ToolBase, ToolChunk


class MemoryContextRead(ToolBase):
    """读取摘要级现有记忆上下文。"""

    name: str = "memory_context_read"
    description: str = (
        "读取当前 user、agent、global Markdown 记忆的摘要级上下文，"
        "用于判断新记忆是否重复、冲突或应该写入哪个 scope。"
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {},
        "required": [],
    }
    is_mcp: bool = False
    is_read_only: bool = True
    is_concurrency_safe: bool = True
    is_external_tool: bool = False
    is_state_injected: bool = False

    def __init__(self, existing_memory_context: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.existing_memory_context = existing_memory_context

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="memory_context_read 只读取摘要级长期记忆。",
            decision_reason="Read-only memory reflection context",
        )

    async def __call__(self, **kwargs: Any) -> ToolChunk:
        return _json_tool_result({
            "existingMemoryContext": self.existing_memory_context,
        })


class MemoryOperationPropose(ToolBase):
    """提交记忆操作候选，不直接写入长期记忆。"""

    name: str = "memory_operation_propose"
    description: str = (
        "提交一个受控记忆操作候选。该工具不会直接写文件；"
        "后端会继续校验、去重、脱敏后再决定是否真正写入。"
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["append", "skip"],
                "description": "append 表示建议新增一条记忆；skip 表示无需写入。",
            },
            "scope": {
                "type": "string",
                "enum": ["user", "agent", "global_preference"],
                "description": "记忆作用域。",
            },
            "section": {
                "type": "string",
                "enum": [
                    "Stable Preferences",
                    "Stable Behaviors",
                    "Profile",
                    "Workspace Rules",
                    "Corrections",
                    "Negative Rules",
                ],
                "description": "Markdown 目标章节。",
            },
            "note": {"type": "string", "description": "简短长期记忆内容。"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "reason": {"type": "string", "description": "为什么值得写入或跳过。"},
        },
        "required": ["operation", "scope", "section", "note"],
    }
    is_mcp: bool = False
    is_read_only: bool = False
    is_concurrency_safe: bool = False
    is_external_tool: bool = False
    is_state_injected: bool = False

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.operations: list[dict[str, Any]] = []

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="memory_operation_propose 只提交候选，不直接写入长期记忆。",
            decision_reason="Internal memory proposal tool",
        )

    async def __call__(
        self,
        operation: str,
        scope: str,
        section: str,
        note: str,
        confidence: float = 0.5,
        reason: str = "",
        **kwargs: Any,
    ) -> ToolChunk:
        payload = {
            "operation": operation,
            "scope": scope,
            "section": section,
            "note": str(note).strip()[:600],
            "confidence": max(0.0, min(float(confidence), 1.0)),
            "reason": str(reason).strip()[:300],
        }
        self.operations.append(payload)
        return _json_tool_result({
            "accepted": True,
            "operation": payload,
            "message": "记忆操作候选已提交，等待后端校验后应用。",
        })


class SkillDraftPropose(ToolBase):
    """提交 Skill 草稿候选，不注册也不绑定 Agent。"""

    name: str = "skill_draft_propose"
    description: str = (
        "提交一个标准 Skill 草稿候选。该工具不会注册 Skill，"
        "也不会把 Skill 自动绑定到任何 Agent。"
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "reusable": {"type": "boolean", "description": "本次工作是否值得沉淀为 Skill。"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "reason": {"type": "string", "description": "判断理由。"},
            "name": {"type": "string", "description": "Skill 名称。"},
            "description": {"type": "string", "description": "Skill 描述。"},
            "trigger": {"type": "string", "description": "触发条件。"},
            "content": {"type": "string", "description": "SKILL.md 风格操作说明。"},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["reusable", "confidence", "reason"],
    }
    is_mcp: bool = False
    is_read_only: bool = False
    is_concurrency_safe: bool = False
    is_external_tool: bool = False
    is_state_injected: bool = False

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.drafts: list[dict[str, Any]] = []

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="skill_draft_propose 只提交 Skill 草稿候选。",
            decision_reason="Internal skill draft proposal tool",
        )

    async def __call__(
        self,
        reusable: bool,
        confidence: float = 0.0,
        reason: str = "",
        name: str = "",
        description: str = "",
        trigger: str = "",
        content: str = "",
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> ToolChunk:
        payload = {
            "reusable": bool(reusable),
            "confidence": max(0.0, min(float(confidence), 1.0)),
            "reason": str(reason).strip()[:500],
            "draft": {
                "name": str(name).strip()[:80],
                "description": str(description).strip()[:300],
                "trigger": str(trigger).strip()[:300],
                "content": str(content).strip()[:8000],
                "tags": [str(tag).strip()[:40] for tag in (tags or []) if str(tag).strip()][:10],
            },
        }
        self.drafts.append(payload)
        return _json_tool_result({
            "accepted": True,
            "candidate": payload,
            "message": "Skill 草稿候选已提交，等待后端校验后处理。",
        })


def _json_tool_result(payload: dict[str, Any]) -> ToolChunk:
    return ToolChunk(
        content=[TextBlock(text=json.dumps(payload, ensure_ascii=False, indent=2))],
        state=ToolResultState.SUCCESS,
        is_last=True,
    )
