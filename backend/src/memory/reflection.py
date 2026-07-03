"""运行后 Markdown 记忆反思提炼。

首版使用保守规则识别用户明确表达的长期偏好、纠正和禁止规则；后续再替换或增强为
基础设施 Agent 模型提炼。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from src.memory.extraction_agents import (
    SkillEvolutionAgentPayload,
    run_memory_reflection_agent,
    run_skill_evolution_agent,
)
from src.memory.markdown_store import MarkdownMemoryStore

MemoryScope = Literal["user", "agent", "global_preference"]
MemorySection = Literal[
    "Stable Preferences",
    "Stable Behaviors",
    "Profile",
    "Workspace Rules",
    "Corrections",
    "Negative Rules",
]

_LONG_TERM_HINTS = (
    "以后",
    "下次",
    "默认",
    "记住",
    "长期",
    "一直",
    "每次",
    "不要再",
    "别再",
    "禁止",
)
_NEGATIVE_HINTS = ("不要", "别", "禁止", "不能", "砍掉", "删除", "下线", "不要再", "别再")
_CORRECTION_HINTS = ("纠正", "不对", "不是", "应该", "改成", "以后改", "需要改")
_GLOBAL_HINTS = (
    "全局",
    "整个工作台",
    "所有agent",
    "所有 agent",
    "所有智能体",
    "全部agent",
    "全部 agent",
    "workspace",
    "项目约定",
    "团队约定",
)
_AGENT_HINTS = (
    "你以后",
    "你下次",
    "你默认",
    "你不要",
    "当前agent",
    "当前 agent",
    "这个agent",
    "这个 agent",
    "该agent",
    "该 agent",
)
_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(api[_-]?key|token|password|secret|cookie)\b\s*[:=]\s*\S+"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\bsk-[a-z0-9_-]{12,}"),
)


@dataclass(frozen=True)
class MemoryReflectionInput:
    """Markdown 记忆反思输入。"""

    session_id: str
    agent_id: str
    agent_name: str
    user_message: str
    assistant_text: str
    user_id: str = "default"
    workspace_id: str = "default"
    source_run_id: str = ""
    source_message_id: str = ""


@dataclass(frozen=True)
class MemoryMarkdownUpdate:
    """一次 Markdown 记忆更新结果。"""

    scope: MemoryScope
    owner_id: str
    section: str
    note: str
    path: str


@dataclass(frozen=True)
class MemoryReflectionResult:
    """运行后记忆反思结果。"""

    updates: list[MemoryMarkdownUpdate] = field(default_factory=list)
    skill_candidate: SkillEvolutionAgentPayload | None = None
    ignored_reason: str = ""


def reflect_completed_run_memory(
    item: MemoryReflectionInput,
    *,
    store: MarkdownMemoryStore | None = None,
) -> MemoryReflectionResult:
    """从成功完成的单轮对话中提炼 Markdown 长期记忆。"""

    user_message = _normalize_text(item.user_message)
    assistant_text = _normalize_text(item.assistant_text)
    if not user_message or not assistant_text:
        return MemoryReflectionResult(ignored_reason="empty_turn")
    if not _has_long_term_intent(user_message):
        return MemoryReflectionResult(ignored_reason="no_long_term_intent")
    if _contains_sensitive_content(user_message):
        return MemoryReflectionResult(ignored_reason="sensitive_content")

    target = _classify_scope(user_message)
    section = _classify_section(user_message, target)
    note = _build_note(user_message, item)
    memory_store = store or MarkdownMemoryStore()
    update = _append_note(memory_store, target, item, section, note)
    if not update:
        return MemoryReflectionResult(ignored_reason="duplicate")
    return MemoryReflectionResult(updates=[update])


async def reflect_completed_run_memory_with_agents(
    item: MemoryReflectionInput,
    *,
    model_name: str,
    fallback_names: list[str] | None = None,
    store: MarkdownMemoryStore | None = None,
) -> MemoryReflectionResult:
    """调用专用系统 Agent 提炼记忆和 Skill 候选，失败时使用规则兜底。"""

    user_message = _normalize_text(item.user_message)
    assistant_text = _normalize_text(item.assistant_text)
    if not user_message or not assistant_text:
        return MemoryReflectionResult(ignored_reason="empty_turn")
    if _contains_sensitive_content(user_message) or _contains_sensitive_content(assistant_text):
        return MemoryReflectionResult(ignored_reason="sensitive_content")

    memory_store = store or MarkdownMemoryStore()
    updates: list[MemoryMarkdownUpdate] = []
    memory_reason = ""
    if _has_long_term_intent(user_message):
        try:
            payload = await run_memory_reflection_agent(
                user_message=user_message,
                assistant_text=assistant_text,
                agent_id=item.agent_id,
                agent_name=item.agent_name,
                existing_memory_context=_build_existing_memory_context(memory_store, item),
                model_name=model_name,
                fallback_names=fallback_names or [],
            )
        except Exception:
            payload = None
        if payload and payload.should_write:
            for memory in payload.memories[:4]:
                if memory.operation == "skip":
                    continue
                update = _append_note(
                    memory_store,
                    memory.scope,
                    item,
                    _safe_section(memory.section, memory.scope),
                    _build_note(memory.note, item),
                )
                if update:
                    updates.append(update)
        memory_reason = payload.reason if payload else ""
        if not updates:
            fallback = reflect_completed_run_memory(item, store=memory_store)
            updates.extend(fallback.updates)
            memory_reason = memory_reason or fallback.ignored_reason

    skill_candidate = None
    if _looks_like_reusable_work(user_message, assistant_text):
        try:
            skill_candidate = await run_skill_evolution_agent(
                user_message=user_message,
                assistant_text=assistant_text,
                agent_id=item.agent_id,
                agent_name=item.agent_name,
                model_name=model_name,
                fallback_names=fallback_names or [],
            )
        except Exception:
            skill_candidate = None
        if skill_candidate and (not skill_candidate.reusable or skill_candidate.confidence < 0.72):
            skill_candidate = None

    if updates or skill_candidate:
        return MemoryReflectionResult(updates=updates, skill_candidate=skill_candidate)
    return MemoryReflectionResult(ignored_reason=memory_reason or "no_memory_or_skill_candidate")


def _has_long_term_intent(text: str) -> bool:
    lowered = text.lower()
    return any(hint in lowered for hint in _LONG_TERM_HINTS)


def _contains_sensitive_content(text: str) -> bool:
    return any(pattern.search(text) for pattern in _SECRET_PATTERNS)


def _classify_scope(text: str) -> MemoryScope:
    lowered = text.lower()
    if any(hint in lowered for hint in _GLOBAL_HINTS):
        return "global_preference"
    if any(hint in lowered for hint in _AGENT_HINTS):
        return "agent"
    return "user"


def _classify_section(text: str, scope: MemoryScope) -> str:
    lowered = text.lower()
    if any(hint in lowered for hint in _NEGATIVE_HINTS):
        return "Negative Rules"
    if any(hint in lowered for hint in _CORRECTION_HINTS):
        return "Corrections"
    if scope == "agent":
        return "Stable Behaviors"
    if scope == "global_preference":
        return "Stable Preferences"
    return "Stable Preferences"


def _safe_section(section: str, scope: MemoryScope) -> MemorySection:
    allowed: set[str] = {
        "Stable Preferences",
        "Stable Behaviors",
        "Profile",
        "Workspace Rules",
        "Corrections",
        "Negative Rules",
    }
    if section in allowed:
        return section  # type: ignore[return-value]
    if scope == "agent":
        return "Stable Behaviors"
    if scope == "global_preference":
        return "Workspace Rules"
    return "Stable Preferences"


def _build_note(text: str, item: MemoryReflectionInput) -> str:
    source = item.source_run_id or item.session_id
    source_part = f" _(source: {source})_" if source else ""
    return f"- {_trim(text, 280)}{source_part}"


def _append_note(
    store: MarkdownMemoryStore,
    scope: MemoryScope,
    item: MemoryReflectionInput,
    section: str,
    note: str,
) -> MemoryMarkdownUpdate | None:
    if scope == "global_preference":
        doc = store.read_global_memory(item.workspace_id)
        content = _insert_note_into_section(doc.content, section, note)
        if content == doc.content:
            return None
        written = store.write_global_memory(item.workspace_id, content)
    elif scope == "agent":
        doc = store.read_agent_memory(item.agent_id)
        content = _insert_note_into_section(doc.content, section, note)
        if content == doc.content:
            return None
        written = store.write_agent_memory(item.agent_id, content)
    else:
        doc = store.read_user_memory(item.user_id)
        content = _insert_note_into_section(doc.content, section, note)
        if content == doc.content:
            return None
        written = store.write_user_memory(item.user_id, content)
    return MemoryMarkdownUpdate(
        scope=scope,
        owner_id=written.owner_id,
        section=section,
        note=note,
        path=written.path,
    )


def _insert_note_into_section(content: str, section: str, note: str) -> str:
    if _normalized_note_text(note) in _normalized_note_text(content):
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


def _normalized_note_text(value: str) -> str:
    value = re.sub(r"_\(source:[^)]+\)_", "", value)
    value = re.sub(r"\s+", "", value)
    return value.lower()


def _normalize_text(value: str) -> str:
    return " ".join(value.split())


def _trim(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + "..."


def _looks_like_reusable_work(user_message: str, assistant_text: str) -> bool:
    text = f"{user_message}\n{assistant_text}".lower()
    if len(assistant_text) < 240:
        return False
    reusable_markers = (
        "skill",
        "技能",
        "复用",
        "流程",
        "步骤",
        "实现",
        "设计",
        "架构",
        "模板",
        "工作流",
        "规范",
        "总结",
    )
    completion_markers = ("完成", "已", "done", "implemented", "added", "通过", "测试")
    return any(marker in text for marker in reusable_markers) and any(marker in text for marker in completion_markers)


def _build_existing_memory_context(store: MarkdownMemoryStore, item: MemoryReflectionInput) -> str:
    """给提炼 Agent 的摘要级现有记忆上下文，用于去重和冲突判断。"""

    sections = []
    for label, content in [
        ("Global preference memory", store.read_global_memory(item.workspace_id).content),
        ("User memory", store.read_user_memory(item.user_id).content),
        ("Agent memory", store.read_agent_memory(item.agent_id).content),
    ]:
        extracted = _extract_markdown_sections_for_reflection(content)
        if extracted:
            sections.append(f"{label}:\n{extracted}")
    return _trim("\n\n".join(sections), 4000)


def _extract_markdown_sections_for_reflection(content: str) -> str:
    parsed = _parse_markdown_sections(content)
    parts = []
    for title in (
        "Summary",
        "Stable Preferences",
        "Stable Behaviors",
        "Profile",
        "Workspace Rules",
        "Corrections",
        "Negative Rules",
    ):
        text = _strip_markdown_comments(parsed.get(title, "")).strip()
        if text:
            parts.append(f"## {title}\n{text}")
    return "\n\n".join(parts)


def _parse_markdown_sections(content: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current = ""
    for line in content.splitlines():
        if line.startswith("# "):
            current = line[2:].strip()
            sections.setdefault(current, [])
            continue
        if current:
            sections[current].append(line)
    return {
        title: "\n".join(lines).strip()
        for title, lines in sections.items()
    }


def _strip_markdown_comments(content: str) -> str:
    lines = [
        line
        for line in content.splitlines()
        if "<!--" not in line and "-->" not in line
    ]
    return "\n".join(lines)
