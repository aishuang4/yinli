"""运行后 Markdown 记忆反思提炼。

首版使用保守规则识别用户明确表达的长期偏好、纠正和禁止规则；后续再替换或增强为
基础设施 Agent 模型提炼。
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Literal

from src.memory.admission import (
    MemoryAdmissionInput,
    MemoryAdmissionScores,
    apply_model_admission_scores,
    evaluate_memory_admission,
)
from src.memory.extraction_agents import (
    SkillEvolutionAgentPayload,
    run_memory_admission_scoring_agent,
    run_memory_reflection_agent,
    run_skill_evolution_agent,
)
from src.memory.markdown_store import MarkdownMemoryStore
from src.memory.sanitizer import sanitize_memory_note_text
from src.memory.settings import get_memory_admission_policy
from src.memory.skill_evolution import (
    SkillEvolutionGateDecision,
    SkillEvolutionRecordInput,
    build_workflow_evidence,
    evaluate_skill_evolution_router_gate,
    is_accepted_skill_candidate,
    persist_skill_evolution_record,
    register_skill_evolution_record,
    rejected_skill_candidate_reason,
)
from src.tools.memory_reflection import MemoryFileToolSet

MemoryScope = Literal["user", "agent", "global_preference"]
MemorySection = Literal[
    "Stable Preferences",
    "Stable Behaviors",
    "Profile",
    "Workspace Rules",
    "Corrections",
    "Negative Rules",
]

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
_MANUAL_SKILL_ACTIONS = (
    "生成",
    "创建",
    "沉淀",
    "提炼",
    "保存",
    "注册",
    "做成",
    "转成",
    "整理成",
    "generate",
    "create",
    "save",
    "register",
)
_MANUAL_SKILL_REFERENCES = (
    "这个",
    "这次",
    "刚才",
    "上面",
    "前面",
    "流程",
    "工作",
    "任务",
    "结果",
    "it",
    "this",
    "that",
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
    project_id: str = ""
    team_id: str = ""
    source_run_id: str = ""
    source_message_id: str = ""
    tool_calls: list[object] = field(default_factory=list)
    runtime_events: list[dict] = field(default_factory=list)
    manual_skill_request: bool = False


@dataclass(frozen=True)
class MemoryMarkdownUpdate:
    """一次 Markdown 记忆更新结果。"""

    scope: MemoryScope
    owner_id: str
    section: str
    note: str
    path: str
    operation: str = "append"


@dataclass(frozen=True)
class MemoryReflectionResult:
    """运行后记忆反思结果。"""

    updates: list[MemoryMarkdownUpdate] = field(default_factory=list)
    skill_candidate: SkillEvolutionAgentPayload | None = None
    skill_evolution_record: dict | None = None
    ignored_reason: str = ""
    admission: object | None = None


def reflect_completed_run_memory(
    item: MemoryReflectionInput,
    *,
    store: MarkdownMemoryStore | None = None,
) -> MemoryReflectionResult:
    """从成功完成的单轮对话中提炼 Markdown 长期记忆。"""

    user_message = _normalize_text(item.user_message)
    assistant_text = _normalize_text(item.assistant_text)
    admission = _admission_for_item(item, user_message, assistant_text)
    if not admission.should_write_markdown:
        return MemoryReflectionResult(ignored_reason=admission.reason, admission=admission)

    target = _classify_scope(user_message)
    section = _classify_section(user_message, target)
    note = _build_note(user_message, item)
    memory_store = store or MarkdownMemoryStore()
    update = _append_note(memory_store, target, item, section, note)
    if not update:
        return MemoryReflectionResult(ignored_reason="duplicate", admission=admission)
    return MemoryReflectionResult(updates=[update], admission=admission)


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
    admission = await _admission_for_item_with_model(
        item,
        user_message,
        assistant_text,
        model_name=model_name,
        fallback_names=fallback_names or [],
    )
    if admission.sensitive or admission.reason == "empty_turn":
        return MemoryReflectionResult(ignored_reason=admission.reason, admission=admission)

    memory_store = store or MarkdownMemoryStore()
    updates: list[MemoryMarkdownUpdate] = []
    memory_reason = ""
    if admission.should_write_markdown:
        reflection_results = await asyncio.gather(
            *[
                _run_scoped_memory_reflection(
                    scope=scope,
                    item=item,
                    user_message=user_message,
                    assistant_text=assistant_text,
                    memory_store=memory_store,
                    model_name=model_name,
                    fallback_names=fallback_names or [],
                )
                for scope in _all_memory_scopes()
            ],
        )
        for scope, memory_file, payload in reflection_results:
            if memory_file.operations:
                updates.extend(_updates_from_memory_file_operations(memory_file.operations))
                memory_reason = payload.reason if payload else memory_reason
                continue
            if payload and payload.should_write:
                for memory in payload.memories[:4]:
                    if memory.operation != "append":
                        continue
                    update = _append_note(
                        memory_store,
                        scope,
                        item,
                        _safe_section(memory.section, scope),
                        _build_note(memory.note, item),
                    )
                    if update:
                        updates.append(update)
            memory_reason = payload.reason if payload else memory_reason
        if not updates:
            fallback = reflect_completed_run_memory(item, store=memory_store)
            updates.extend(fallback.updates)
            memory_reason = memory_reason or fallback.ignored_reason

    skill_candidate = None
    skill_evolution_record = None
    gate = _skill_evolution_gate_for_item(
        item=item,
        user_message=user_message,
        assistant_text=assistant_text,
        admission_allows_skill=admission.should_evaluate_skill,
        admission_reason=admission.reason,
    )
    workflow_evidence = build_workflow_evidence(
        tool_calls=item.tool_calls,
        runtime_events=item.runtime_events,
    )
    if gate.should_evaluate:
        try:
            skill_candidate = await run_skill_evolution_agent(
                user_message=user_message,
                assistant_text=assistant_text,
                agent_id=item.agent_id,
                agent_name=item.agent_name,
                workflow_evidence=workflow_evidence,
                router_gate=gate,
                model_name=model_name,
                fallback_names=fallback_names or [],
                invocation_key=_reflection_invocation_key(item, "skill"),
            )
        except Exception as exc:
            skill_candidate = None
            skill_evolution_record = _persist_skill_evolution_record(
                item=item,
                gate=gate,
                workflow_evidence=workflow_evidence,
                candidate=None,
                status="failed",
                risk_notes=f"SkillEvolutionAgent 调用失败：{type(exc).__name__}",
            )
        if skill_evolution_record is None:
            accepted = is_accepted_skill_candidate(skill_candidate)
            skill_evolution_record = _persist_skill_evolution_record(
                item=item,
                gate=gate,
                workflow_evidence=workflow_evidence,
                candidate=skill_candidate,
                status="draft" if accepted else "skipped",
                risk_notes="" if accepted else rejected_skill_candidate_reason(skill_candidate),
            )
            if not accepted:
                skill_candidate = None
            elif skill_evolution_record:
                registered_record, _reason = register_skill_evolution_record(skill_evolution_record)
                if registered_record:
                    skill_evolution_record = registered_record
    else:
        skill_evolution_record = _persist_skill_evolution_record(
            item=item,
            gate=gate,
            workflow_evidence=workflow_evidence,
            candidate=None,
            status="skipped",
            risk_notes=gate.reason,
        )

    if updates or skill_candidate or skill_evolution_record:
        return MemoryReflectionResult(
            updates=updates,
            skill_candidate=skill_candidate,
            skill_evolution_record=skill_evolution_record,
            admission=admission,
        )
    return MemoryReflectionResult(
        ignored_reason=memory_reason or "no_memory_or_skill_candidate",
        admission=admission,
    )


def is_manual_skill_generation_request(text: str) -> bool:
    """判断用户是否显式要求把前序工作生成 Skill。"""

    normalized = _normalize_text(text).lower()
    if not normalized:
        return False
    has_skill_marker = any(marker in normalized for marker in ("skill", "skills", "技能"))
    if not has_skill_marker:
        return False
    has_action = any(marker in normalized for marker in _MANUAL_SKILL_ACTIONS)
    if not has_action:
        return False
    return len(normalized) <= 120 or any(marker in normalized for marker in _MANUAL_SKILL_REFERENCES)


def _skill_evolution_gate_for_item(
    *,
    item: MemoryReflectionInput,
    user_message: str,
    assistant_text: str,
    admission_allows_skill: bool = True,
    admission_reason: str = "",
) -> SkillEvolutionGateDecision:
    """为自动或手动 Skill 提炼生成 gate 决策。"""

    if not admission_allows_skill:
        return SkillEvolutionGateDecision(
            shouldEvaluate=False,
            reason=f"MemoryAdmissionGate 跳过 Skill 自进化：{admission_reason or 'not_admitted'}。",
            risk="skip",
            signals=["memory_admission_gate"],
        )
    if item.manual_skill_request:
        return SkillEvolutionGateDecision(
            shouldEvaluate=True,
            reason="用户显式要求把前序完成工作生成 Skill。",
            risk="low",
            signals=["manual_skill_request"],
        )
    return evaluate_skill_evolution_router_gate(
        user_message=user_message,
        assistant_text=assistant_text,
        tool_calls=item.tool_calls,
        runtime_events=item.runtime_events,
    )


def _admission_for_item(
    item: MemoryReflectionInput,
    user_message: str,
    assistant_text: str,
):
    return evaluate_memory_admission(
        MemoryAdmissionInput(
            user_message=user_message,
            assistant_text=assistant_text,
            tool_calls=item.tool_calls,
            runtime_events=item.runtime_events,
            manual_skill_request=item.manual_skill_request,
        )
    )


async def _admission_for_item_with_model(
    item: MemoryReflectionInput,
    user_message: str,
    assistant_text: str,
    *,
    model_name: str,
    fallback_names: list[str],
):
    """规则准入 + 可选模型评分增强。"""

    decision = _admission_for_item(item, user_message, assistant_text)
    if not decision.policy.enable_model_scoring or decision.reason in {"empty_turn", "sensitive_content"}:
        return decision
    try:
        payload = await asyncio.wait_for(
            run_memory_admission_scoring_agent(
                user_message=decision.redacted_user_message or user_message,
                assistant_text=decision.redacted_assistant_text or assistant_text,
                agent_id=item.agent_id,
                agent_name=item.agent_name,
                rule_scores=decision.scores,
                rule_signals=decision.signals,
                policy=decision.policy,
                model_name=model_name,
                fallback_names=fallback_names,
                invocation_key=_reflection_invocation_key(item, "admission"),
            ),
            timeout=decision.policy.model_timeout_seconds,
        )
    except Exception as exc:
        return apply_model_admission_scores(
            decision,
            scores=None,
            error=f"{type(exc).__name__}: {str(exc)[:160]}",
        )
    if not payload:
        return apply_model_admission_scores(decision, scores=None, error="empty_model_payload")
    return apply_model_admission_scores(
        decision,
        scores=MemoryAdmissionScores(
            history=payload.history,
            markdown=payload.markdown,
            skill=payload.skill,
        ),
        reason=payload.reason,
    )


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
    note = _sanitize_memory_note(note)
    if not note:
        return None
    policy = get_memory_admission_policy()
    if scope == "global_preference":
        doc = store.read_global_memory(item.workspace_id)
        content = _insert_note_into_section(doc.content, section, note)
        if content == doc.content:
            return None
        content = _prepare_markdown_memory_content(content, policy=policy)
        written = _write_with_retry(lambda value: store.write_global_memory(item.workspace_id, value), content, policy.markdown_write_retries)
    elif scope == "agent":
        doc = store.read_agent_memory(item.agent_id)
        content = _insert_note_into_section(doc.content, section, note)
        if content == doc.content:
            return None
        content = _prepare_markdown_memory_content(content, policy=policy)
        written = _write_with_retry(lambda value: store.write_agent_memory(item.agent_id, value), content, policy.markdown_write_retries)
    else:
        doc = store.read_user_memory(item.user_id)
        content = _insert_note_into_section(doc.content, section, note)
        if content == doc.content:
            return None
        content = _prepare_markdown_memory_content(content, policy=policy)
        written = _write_with_retry(lambda value: store.write_user_memory(item.user_id, value), content, policy.markdown_write_retries)
    return MemoryMarkdownUpdate(
        scope=scope,
        owner_id=written.owner_id,
        section=section,
        note=note,
        path=written.path,
    )


def _all_memory_scopes() -> tuple[MemoryScope, MemoryScope, MemoryScope]:
    return ("user", "agent", "global_preference")


def _persist_skill_evolution_record(
    *,
    item: MemoryReflectionInput,
    gate,
    workflow_evidence,
    candidate: SkillEvolutionAgentPayload | None,
    status: str,
    risk_notes: str,
) -> dict | None:
    """保存 Skill 自进化记录，失败时不影响主对话和记忆提炼。"""

    try:
        return persist_skill_evolution_record(
            SkillEvolutionRecordInput(
                session_id=item.session_id,
                agent_id=item.agent_id,
                agent_name=item.agent_name,
                user_message=item.user_message,
                assistant_text=item.assistant_text,
                user_id=item.user_id,
                workspace_id=item.workspace_id,
                project_id=item.project_id,
                team_id=item.team_id,
                source_run_id=item.source_run_id,
                source_message_id=item.source_message_id,
                router_gate=gate,
                workflow_evidence=workflow_evidence,
                candidate=candidate,
                status=status,
                risk_notes=risk_notes,
            )
        )
    except Exception:
        return None


async def _run_scoped_memory_reflection(
    *,
    scope: MemoryScope,
    item: MemoryReflectionInput,
    user_message: str,
    assistant_text: str,
    memory_store: MarkdownMemoryStore,
    model_name: str,
    fallback_names: list[str],
) -> tuple[MemoryScope, MemoryFileToolSet, object | None]:
    memory_file = _build_memory_file_tool_set(memory_store, item, scope)
    try:
        payload = await run_memory_reflection_agent(
            user_message=user_message,
            assistant_text=assistant_text,
            agent_id=item.agent_id,
            agent_name=item.agent_name,
            memory_file=memory_file,
            model_name=model_name,
            fallback_names=fallback_names,
            invocation_key=_reflection_invocation_key(item, scope),
        )
    except Exception:
        payload = None
    return scope, memory_file, payload


def _build_memory_file_tool_set(
    store: MarkdownMemoryStore,
    item: MemoryReflectionInput,
    scope: MemoryScope,
) -> MemoryFileToolSet:
    if scope == "global_preference":
        doc = store.read_global_memory(item.workspace_id)

        def read_content() -> str:
            return store.read_global_memory(item.workspace_id).content

        def write_content(content: str) -> None:
            store.write_global_memory(item.workspace_id, content)

    elif scope == "agent":
        doc = store.read_agent_memory(item.agent_id)

        def read_content() -> str:
            return store.read_agent_memory(item.agent_id).content

        def write_content(content: str) -> None:
            store.write_agent_memory(item.agent_id, content)

    else:
        doc = store.read_user_memory(item.user_id)

        def read_content() -> str:
            return store.read_user_memory(item.user_id).content

        def write_content(content: str) -> None:
            store.write_user_memory(item.user_id, content)

    return MemoryFileToolSet(
        scope=scope,
        owner_id=doc.owner_id,
        file_path=doc.path,
        read_content=read_content,
        write_content=write_content,
    )


def _updates_from_memory_file_operations(operations: list[dict]) -> list[MemoryMarkdownUpdate]:
    updates = []
    for operation in operations:
        op = str(operation.get("operation") or "append")
        note = (
            str(operation.get("note") or "")
            or str(operation.get("replacementText") or "")
            or str(operation.get("exactText") or "")
        )
        updates.append(
            MemoryMarkdownUpdate(
                scope=str(operation.get("scope") or "user"),  # type: ignore[arg-type]
                owner_id=str(operation.get("ownerId") or ""),
                section=str(operation.get("section") or ""),
                note=note,
                path=str(operation.get("filePath") or ""),
                operation=op,
            ),
        )
    return updates


def _reflection_invocation_key(item: MemoryReflectionInput, target: str) -> str:
    raw = "|".join([
        item.session_id,
        item.source_run_id,
        item.source_message_id,
        item.agent_id,
        item.user_id,
        item.workspace_id,
        target,
        item.user_message[:160],
    ])
    return sha256(raw.encode("utf-8")).hexdigest()[:16]


def _insert_note_into_section(content: str, section: str, note: str) -> str:
    normalized_note = _normalized_note_text(note)
    normalized_content = _normalized_note_text(content)
    if normalized_note in normalized_content:
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


def _sanitize_memory_note(note: str) -> str:
    text = sanitize_memory_note_text(str(note or "").strip())
    if not text:
        return ""
    return text


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
        if _normalized_note_text(line) == _normalized_note_text(note):
            return content
        lines[index] = note
        return "\n".join(lines).rstrip() + "\n"
    return content


def _conflict_key(value: str) -> str:
    normalized = _normalized_note_text(value)
    if "默认" in normalized and ("中文" in normalized or "英文" in normalized):
        return "default_language"
    if "简洁" in normalized and ("详细" in normalized or "详细解释" in normalized):
        return "answer_detail_level"
    if "planruntime" in normalized:
        return "plan_runtime_rule"
    if "memoryruntime" in normalized:
        return "memory_runtime_rule"
    return ""


def _prepare_markdown_memory_content(content: str, *, policy) -> str:
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


def _collapse_blank_lines(content: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", content).rstrip() + "\n"


def _write_with_retry(write_fn, content: str, retries: int):
    last_exc: Exception | None = None
    for _attempt in range(max(retries, 0) + 1):
        try:
            return write_fn(content)
        except Exception as exc:
            last_exc = exc
    if last_exc:
        raise last_exc
    return write_fn(content)


def _normalized_note_text(value: str) -> str:
    value = re.sub(r"_\(source:[^)]+\)_", "", value)
    value = re.sub(r"\(source:[^)]+\)", "", value)
    value = re.sub(r"source:[\w:./-]+", "", value, flags=re.IGNORECASE)
    value = re.sub(r"^-", "", value.strip())
    value = re.sub(r"\s+", "", value)
    return value.lower()


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


def _normalize_text(value: str) -> str:
    return " ".join(value.split())


def _trim(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + "..."
