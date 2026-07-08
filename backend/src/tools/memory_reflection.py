"""记忆提炼专用内部工具。

这些工具只给系统基础设施 Agent 使用。每组工具绑定一个指定 Markdown 记忆文件，模型不能
传入任意路径，只能通过 memory_read / memory_add / memory_update / memory_delete 操作该文件。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from agentscope.message import TextBlock, ToolResultState
from agentscope.permission import PermissionBehavior, PermissionContext, PermissionDecision
from agentscope.tool import ToolBase, ToolChunk
from src.memory.sanitizer import sanitize_memory_note_text
from src.memory.settings import get_memory_admission_policy

ReadMemoryContent = Callable[[], str]
WriteMemoryContent = Callable[[str], None]


@dataclass
class MemoryFileToolSet:
    """绑定到单个 Markdown 记忆文件的一组内部工具。"""

    scope: str
    owner_id: str
    file_path: str
    read_content: ReadMemoryContent
    write_content: WriteMemoryContent
    operations: list[dict[str, Any]] = field(default_factory=list)

    def tools(self) -> list[ToolBase]:
        """返回注入 Agent 的工具实例。"""

        return [
            MemoryRead(self),
            MemoryAdd(self),
            MemoryUpdate(self),
            MemoryDelete(self),
        ]


class _BoundMemoryTool(ToolBase):
    """绑定指定记忆文件的工具基类。"""

    is_mcp: bool = False
    is_external_tool: bool = False
    is_state_injected: bool = False

    def __init__(self, memory_file: MemoryFileToolSet, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.memory_file = memory_file

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message=f"{self.name} 只能操作已绑定的记忆文件: {self.memory_file.file_path}",
            decision_reason="Bound internal memory tool",
        )


class MemoryRead(_BoundMemoryTool):
    """读取绑定记忆文件的完整 Markdown 内容。"""

    name: str = "memory_read"
    description: str = (
        "读取当前绑定的 Markdown 记忆文件完整内容。工具已绑定唯一文件，"
        "不能读取其他路径。修改前必须先调用它。"
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {},
        "required": [],
    }
    is_read_only: bool = True
    is_concurrency_safe: bool = True

    async def __call__(self, **kwargs: Any) -> ToolChunk:
        return _json_tool_result({
            "scope": self.memory_file.scope,
            "ownerId": self.memory_file.owner_id,
            "filePath": self.memory_file.file_path,
            "content": self.memory_file.read_content(),
        })


class MemoryAdd(_BoundMemoryTool):
    """向绑定记忆文件的指定章节添加一条记忆。"""

    name: str = "memory_add"
    description: str = (
        "向当前绑定的 Markdown 记忆文件添加一条记忆。"
        "只能追加到指定 section，不能操作其他文件。"
        "note 和 reason 必须使用中文记录，必要技术标识符可保留原文。"
        "重要稳定实体可用 Obsidian 链接格式 [[类型:名称]]，例如 [[规则:禁止恢复旧 Plan Runtime]]。"
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "section": {
                "type": "string",
                "enum": [
                    "Summary",
                    "Stable Preferences",
                    "Stable Behaviors",
                    "Profile",
                    "Workspace Rules",
                    "Corrections",
                    "Negative Rules",
                    "Source References",
                ],
                "description": "要追加到的 Markdown 一级章节。",
            },
            "note": {"type": "string", "description": "一条简短、稳定、可复用的中文记忆。"},
            "reason": {"type": "string", "description": "中文添加原因。"},
        },
        "required": ["section", "note"],
    }
    is_read_only: bool = False
    is_concurrency_safe: bool = False

    async def __call__(
        self,
        section: str,
        note: str,
        reason: str = "",
        **kwargs: Any,
    ) -> ToolChunk:
        normalized_note = _normalize_note(note)
        if not normalized_note:
            return _json_tool_result({"updated": False, "reason": "empty_note"})
        content = self.memory_file.read_content()
        next_content = _insert_note_into_section(content, section, normalized_note)
        if next_content == content:
            return _json_tool_result({"updated": False, "reason": "duplicate"})
        next_content = _prepare_markdown_memory_content(next_content)
        self.memory_file.write_content(next_content)
        operation = {
            "operation": "append",
            "scope": self.memory_file.scope,
            "ownerId": self.memory_file.owner_id,
            "filePath": self.memory_file.file_path,
            "section": section,
            "note": normalized_note,
            "reason": str(reason).strip()[:300],
        }
        self.memory_file.operations.append(operation)
        return _json_tool_result({"updated": True, "operation": operation})


class MemoryUpdate(_BoundMemoryTool):
    """精确替换绑定记忆文件中的一段文本。"""

    name: str = "memory_update"
    description: str = (
        "精确修改当前绑定 Markdown 记忆文件中的一条或一段记忆。"
        "必须提供文件里已存在的 exactText，工具只做一次精确替换。"
        "replacementText 和 reason 必须使用中文记录，必要技术标识符可保留原文。"
        "重要稳定实体可用 Obsidian 链接格式 [[类型:名称]]，例如 [[项目:Agent Workstation]]。"
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "exactText": {"type": "string", "description": "文件中已经存在、需要替换的完整文本。"},
            "replacementText": {"type": "string", "description": "替换后的中文文本。"},
            "reason": {"type": "string", "description": "中文修改原因。"},
        },
        "required": ["exactText", "replacementText"],
    }
    is_read_only: bool = False
    is_concurrency_safe: bool = False

    async def __call__(
        self,
        exactText: str,
        replacementText: str,
        reason: str = "",
        **kwargs: Any,
    ) -> ToolChunk:
        exact = str(exactText).strip()
        replacement = str(replacementText).strip()
        if not exact or not replacement:
            return _json_tool_result({"updated": False, "reason": "empty_text"})
        content = self.memory_file.read_content()
        if exact not in content:
            return _json_tool_result({"updated": False, "reason": "exact_text_not_found"})
        next_content = content.replace(exact, replacement, 1)
        next_content = _prepare_markdown_memory_content(next_content)
        self.memory_file.write_content(next_content)
        operation = {
            "operation": "update",
            "scope": self.memory_file.scope,
            "ownerId": self.memory_file.owner_id,
            "filePath": self.memory_file.file_path,
            "exactText": exact[:600],
            "replacementText": replacement[:600],
            "reason": str(reason).strip()[:300],
        }
        self.memory_file.operations.append(operation)
        return _json_tool_result({"updated": True, "operation": operation})


class MemoryDelete(_BoundMemoryTool):
    """从绑定记忆文件中删除一段精确文本。"""

    name: str = "memory_delete"
    description: str = (
        "从当前绑定 Markdown 记忆文件删除一条或一段记忆。"
        "必须提供文件里已存在的 exactText，工具只删除一次精确匹配。"
        "reason 必须使用中文记录。"
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "exactText": {"type": "string", "description": "文件中已经存在、需要删除的完整文本。"},
            "reason": {"type": "string", "description": "中文删除原因。"},
        },
        "required": ["exactText"],
    }
    is_read_only: bool = False
    is_concurrency_safe: bool = False

    async def __call__(
        self,
        exactText: str,
        reason: str = "",
        **kwargs: Any,
    ) -> ToolChunk:
        exact = str(exactText).strip()
        if not exact:
            return _json_tool_result({"updated": False, "reason": "empty_text"})
        content = self.memory_file.read_content()
        if exact not in content:
            return _json_tool_result({"updated": False, "reason": "exact_text_not_found"})
        next_content = content.replace(exact, "", 1)
        self.memory_file.write_content(_prepare_markdown_memory_content(next_content))
        operation = {
            "operation": "delete",
            "scope": self.memory_file.scope,
            "ownerId": self.memory_file.owner_id,
            "filePath": self.memory_file.file_path,
            "exactText": exact[:600],
            "reason": str(reason).strip()[:300],
        }
        self.memory_file.operations.append(operation)
        return _json_tool_result({"updated": True, "operation": operation})


class SkillDraftPropose(ToolBase):
    """提交 Skill 草稿候选，不注册也不绑定 Agent。"""

    name: str = "skill_draft_propose"
    description: str = (
        "提交一个标准 Skill 草稿候选。该工具不会注册 Skill，"
        "也不会把 Skill 自动绑定到任何 Agent。"
        "reason、name、description、trigger、content 必须使用中文记录，必要技术标识符可保留原文。"
        "只有原子化 Skill 才能提交 draft；过大流程应设置 requiresWorkflow 并给出 suggestedSplit。"
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "reusable": {"type": "boolean", "description": "本次工作是否值得沉淀为 Skill。"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "granularity": {
                "type": "string",
                "enum": ["too_small", "atomic", "too_broad"],
                "description": "Skill 颗粒度判断。",
            },
            "requiresWorkflow": {"type": "boolean", "description": "是否应交给 Workflow 编排多个原子 Skill。"},
            "reason": {"type": "string", "description": "中文判断理由。"},
            "suggestedSplit": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "trigger": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                },
            },
            "variableFlow": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "source": {"type": "string"},
                        "target": {"type": "string"},
                        "variable": {"type": "string"},
                    },
                },
                "description": "需要在 Skill 中保留的变量传递关系。",
            },
            "name": {"type": "string", "description": "中文 Skill 名称。"},
            "description": {"type": "string", "description": "中文 Skill 描述。"},
            "trigger": {"type": "string", "description": "中文触发条件。"},
            "content": {"type": "string", "description": "中文 SKILL.md 风格操作说明。"},
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
        granularity: str = "atomic",
        requiresWorkflow: bool = False,
        reason: str = "",
        suggestedSplit: list[dict[str, Any]] | None = None,
        variableFlow: list[dict[str, Any]] | None = None,
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
            "granularity": granularity if granularity in {"too_small", "atomic", "too_broad"} else "atomic",
            "requiresWorkflow": bool(requiresWorkflow),
            "reason": str(reason).strip()[:500],
            "suggestedSplit": [
                {
                    "name": str(item.get("name") or "").strip()[:80],
                    "trigger": str(item.get("trigger") or "").strip()[:300],
                    "reason": str(item.get("reason") or "").strip()[:300],
                }
                for item in (suggestedSplit or [])
                if isinstance(item, dict)
            ][:8],
            "variableFlow": [
                {
                    "source": str(item.get("source") or "").strip()[:120],
                    "target": str(item.get("target") or "").strip()[:120],
                    "variable": str(item.get("variable") or "").strip()[:80],
                }
                for item in (variableFlow or [])
                if isinstance(item, dict)
            ][:16],
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


def _normalize_note(note: str) -> str:
    normalized = sanitize_memory_note_text(" ".join(str(note).split()))
    if not normalized:
        return ""
    return normalized if normalized.startswith("- ") else f"- {normalized}"


def _insert_note_into_section(content: str, section: str, note: str) -> str:
    if _normalized_text(note) in _normalized_text(content):
        return content
    conflict_merged = _merge_conflicting_note(content, note)
    if conflict_merged != content:
        return conflict_merged
    if _is_near_duplicate_note(content, note):
        return content
    lines = content.rstrip().splitlines()
    heading = f"# {section}"
    try:
        start = lines.index(heading)
    except ValueError:
        lines.extend(["", heading, ""])
        start = len(lines) - 2

    insert_at = len(lines)
    for index in range(start + 1, len(lines)):
        if lines[index].startswith("# "):
            insert_at = index
            break
    while insert_at > start + 1 and not lines[insert_at - 1].strip():
        insert_at -= 1
    lines[insert_at:insert_at] = ["", note]
    return "\n".join(lines).rstrip() + "\n"


def _merge_conflicting_note(content: str, note: str) -> str:
    conflict_key = _conflict_key(note)
    if not conflict_key:
        return content
    lines = content.splitlines()
    current_section = ""
    for index, line in enumerate(lines):
        if line.startswith("# "):
            current_section = line[2:].strip()
            continue
        if current_section == "Summary":
            continue
        if not line.strip().startswith("-"):
            continue
        if _conflict_key(line) != conflict_key:
            continue
        lines[index] = note
        return "\n".join(lines).rstrip() + "\n"
    return content


def _conflict_key(value: str) -> str:
    normalized = _normalized_text(value)
    if "默认" in normalized and ("中文" in normalized or "英文" in normalized):
        return "default_language"
    if "简洁" in normalized and "详细" in normalized:
        return "answer_detail_level"
    if "planruntime" in normalized:
        return "plan_runtime_rule"
    if "memoryruntime" in normalized:
        return "memory_runtime_rule"
    return ""


def _prepare_markdown_memory_content(content: str) -> str:
    policy = get_memory_admission_policy()
    content = _collapse_blank_lines(content)
    return _rebuild_summary_section(
        content,
        max_items_per_section=policy.summary_max_items_per_section,
        max_chars=policy.summary_max_chars,
    )


def _rebuild_summary_section(content: str, *, max_items_per_section: int, max_chars: int) -> str:
    sections = _parse_markdown_sections(content)
    summary_items: list[str] = []
    for section in (
        "Stable Preferences",
        "Stable Behaviors",
        "Profile",
        "Workspace Rules",
        "Corrections",
        "Negative Rules",
    ):
        items = [
            _strip_source_reference(line.strip())
            for line in sections.get(section, [])
            if line.strip().startswith("-")
        ][:max_items_per_section]
        for item in items:
            if item and item not in summary_items:
                summary_items.append(item)
    summary = "\n".join(summary_items)
    if len(summary) > max_chars:
        summary = summary[:max_chars].rstrip() + "..."
    summary_lines = ["# Summary", "", "<!-- auto-generated summary; edit sections below for source of truth. -->"]
    if summary:
        summary_lines.extend(["", summary])
    return _replace_section(content, "Summary", "\n".join(summary_lines).rstrip())


def _parse_markdown_sections(content: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current = ""
    for line in content.splitlines():
        if line.startswith("# "):
            current = line[2:].strip()
            sections.setdefault(current, [])
            continue
        if current:
            sections[current].append(line)
    return sections


def _replace_section(content: str, section: str, replacement: str) -> str:
    lines = content.rstrip().splitlines()
    heading = f"# {section}"
    try:
        start = lines.index(heading)
    except ValueError:
        return replacement.rstrip() + "\n\n" + content.rstrip() + "\n"
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if lines[index].startswith("# "):
            end = index
            break
    next_lines = lines[:start] + replacement.rstrip().splitlines() + [""] + lines[end:]
    return "\n".join(next_lines).rstrip() + "\n"


def _strip_source_reference(value: str) -> str:
    value = re.sub(r"_\(source:[^)]+\)_", "", value)
    value = re.sub(r"\(source:[^)]+\)", "", value)
    return value.strip()


def _normalized_text(value: str) -> str:
    text = re.sub(r"_\(source:[^)]+\)_", "", str(value))
    text = re.sub(r"\(source:[^)]+\)", "", text)
    return "".join(text.lower().split())


def _is_near_duplicate_note(content: str, note: str) -> bool:
    note_tokens = _dedupe_tokens(note)
    if not note_tokens:
        return False
    for line in content.splitlines():
        if not line.strip().startswith("-"):
            continue
        line_tokens = _dedupe_tokens(line)
        if not line_tokens:
            continue
        overlap = len(note_tokens & line_tokens) / max(len(note_tokens), 1)
        if overlap >= 0.82:
            return True
    return False


def _dedupe_tokens(value: str) -> set[str]:
    cleaned = re.sub(r"_\(source:[^)]+\)_", "", value)
    return {
        token.lower()
        for token in re.findall(r"[A-Za-z0-9_]{2,}|[\u4e00-\u9fff]{2,}", cleaned)
        if token.lower() not in {"source", "run", "session"}
    }


def _collapse_blank_lines(content: str) -> str:
    lines: list[str] = []
    previous_blank = False
    for line in content.splitlines():
        blank = not line.strip()
        if blank and previous_blank:
            continue
        lines.append(line)
        previous_blank = blank
    return "\n".join(lines).rstrip() + "\n"


def _json_tool_result(payload: dict[str, Any]) -> ToolChunk:
    return ToolChunk(
        content=[TextBlock(text=json.dumps(payload, ensure_ascii=False, indent=2))],
        state=ToolResultState.SUCCESS,
        is_last=True,
    )
