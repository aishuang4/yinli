"""旧三层 Markdown 记忆的改写、候选和章节整理。

Skill 自进化不在这里。编排入口仍是 reflection.reflect_completed_run_memory_with_agents。
"""

from __future__ import annotations

import re

from src.memory.markdown_store import MarkdownMemoryStore
from src.memory.reflection import (
    MemoryMarkdownUpdate,
    MemoryReflectionInput,
    MemoryReflectionResult,
    MemoryScope,
    MemorySection,
    _admission_for_item,
    _normalize_text,
    _trim,
)
from src.memory.sanitizer import sanitize_memory_note_text
from src.memory.settings import get_memory_admission_policy
from src.tools.memory_reflection import MemoryFileToolSet


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
    update, candidate = _route_note(memory_store, target, item, section, note, reason="规则识别的长期偏好 / 规则", confidence=0.5)
    if not update and not candidate:
        return MemoryReflectionResult(ignored_reason="duplicate", admission=admission)
    return MemoryReflectionResult(
        updates=[update] if update else [],
        candidates=[candidate] if candidate else [],
        admission=admission,
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
        doc = store.read_global_memory()
        content = _insert_note_into_section(doc.content, section, note)
        if content == doc.content:
            return None
        content = _prepare_markdown_memory_content(content, policy=policy)
        written = _write_with_retry(store.write_global_memory, content, policy.markdown_write_retries)
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


# 一次提炼最多应用的操作数（与 prompt 中的 maxOperationsTotal 一致）。
MAX_MEMORY_OPERATIONS_PER_SCOPE = 3


MAX_MEMORY_OPERATIONS_TOTAL = 6


async def _apply_memory_operations(
    memories: list,
    *,
    memory_files: dict[str, MemoryFileToolSet],
    item: MemoryReflectionInput,
) -> tuple[list[MemoryMarkdownUpdate], list[dict]]:
    """按 scope 应用模型给出的操作：agent 层直接写文件，需确认的层进候选队列。"""

    policy = get_memory_admission_policy()
    updates: list[MemoryMarkdownUpdate] = []
    candidates: list[dict] = []
    per_scope: dict[str, int] = {}
    total = 0
    for memory in memories:
        if total >= MAX_MEMORY_OPERATIONS_TOTAL:
            break
        scope = str(getattr(memory, "scope", "") or "")
        operation = str(getattr(memory, "operation", "append") or "append")
        if scope not in memory_files or operation == "skip":
            continue
        if per_scope.get(scope, 0) >= MAX_MEMORY_OPERATIONS_PER_SCOPE:
            continue
        note = _sanitize_memory_note(str(getattr(memory, "note", "") or ""))
        exact_text = str(getattr(memory, "exact_text", "") or "").strip()
        replacement = _sanitize_memory_note(str(getattr(memory, "replacement_text", "") or "")).lstrip("- ").strip()
        reason = str(getattr(memory, "reason", "") or "")
        confidence = float(getattr(memory, "confidence", 0.5) or 0.0)
        section = _safe_section(str(getattr(memory, "section", "") or ""), scope)  # type: ignore[arg-type]
        if operation == "append" and not note:
            continue
        if operation in {"update", "delete"} and not exact_text:
            continue
        if operation == "update" and not replacement:
            continue
        per_scope[scope] = per_scope.get(scope, 0) + 1
        total += 1
        if policy.requires_confirmation(scope):
            candidate = _enqueue_candidate(
                item,
                scope=scope,
                owner_id=memory_files[scope].owner_id,
                section=section,
                operation=operation,
                note=note,
                exact_text=exact_text,
                replacement_text=replacement,
                reason=reason,
                confidence=confidence,
            )
            if candidate:
                candidates.append(candidate)
            continue
        applied = await _apply_operation_to_file(
            memory_files[scope],
            operation=operation,
            section=section,
            note=note,
            exact_text=exact_text,
            replacement_text=replacement,
            reason=reason,
        )
        if applied:
            updates.append(applied)
    return updates, candidates


async def _apply_operation_to_file(
    memory_file: MemoryFileToolSet,
    *,
    operation: str,
    section: str,
    note: str,
    exact_text: str,
    replacement_text: str,
    reason: str,
) -> MemoryMarkdownUpdate | None:
    """用绑定文件的内部工具真正执行一次操作（带去重 / 冲突合并 / Summary 重建）。"""

    from src.tools.memory_reflection import MemoryAdd, MemoryDelete, MemoryUpdate

    before = len(memory_file.operations)
    try:
        if operation == "append":
            await MemoryAdd(memory_file)(section=section, note=note, reason=reason)
        elif operation == "update":
            await MemoryUpdate(memory_file)(exactText=exact_text, replacementText=replacement_text, reason=reason)
        elif operation == "delete":
            await MemoryDelete(memory_file)(exactText=exact_text, reason=reason)
        else:
            return None
    except Exception:
        return None
    new_ops = memory_file.operations[before:]
    if not new_ops:
        return None
    return _updates_from_memory_file_operations(new_ops)[-1]


def _enqueue_candidate(
    item: MemoryReflectionInput,
    *,
    scope: str,
    owner_id: str,
    section: str,
    operation: str,
    note: str,
    exact_text: str,
    replacement_text: str,
    reason: str,
    confidence: float,
) -> dict | None:
    """把一条需要用户确认的操作写进候选队列；同内容的 pending 候选只留一条。"""

    from src.storage.database import create_memory_markdown_candidate, find_pending_memory_markdown_candidate

    try:
        if find_pending_memory_markdown_candidate(scope=scope, owner_id=owner_id, note=note, exact_text=exact_text):
            return None
        return create_memory_markdown_candidate({
            "scope": scope,
            "ownerId": owner_id,
            "section": section,
            "operation": operation,
            "note": note,
            "exactText": exact_text,
            "replacementText": replacement_text,
            "reason": reason,
            "confidence": confidence,
            "agentId": item.agent_id,
            "agentName": item.agent_name,
            "project_slug": item.project_slug,
            "sourceSessionId": item.session_id,
            "sourceRunId": item.source_run_id,
        })
    except Exception:
        return None


def _route_note(
    store: MarkdownMemoryStore,
    scope: MemoryScope,
    item: MemoryReflectionInput,
    section: str,
    note: str,
    *,
    reason: str,
    confidence: float,
) -> tuple[MemoryMarkdownUpdate | None, dict | None]:
    """规则兜底路径的写入分流：需确认的 scope 进队列，否则直接 append。"""

    policy = get_memory_admission_policy()
    if policy.requires_confirmation(scope):
        owner = _owner_id_for_scope(item, scope)
        return None, _enqueue_candidate(
            item,
            scope=scope,
            owner_id=owner,
            section=section,
            operation="append",
            note=_sanitize_memory_note(note),
            exact_text="",
            replacement_text="",
            reason=reason,
            confidence=confidence,
        )
    return _append_note(store, scope, item, section, note), None


def _owner_id_for_scope(item: MemoryReflectionInput, scope: str) -> str:
    if scope == "global_preference":
        return "tenant"
    if scope == "agent":
        return item.agent_id
    return item.user_id


async def apply_markdown_candidate(record: dict, *, store: MarkdownMemoryStore | None = None) -> tuple[bool, str]:
    """用户采纳候选：按记录里的 scope / owner 绑定文件并执行操作。返回 (成功, 原因)。"""

    scope = str(record.get("scope") or "")
    if scope not in {"user", "agent", "global_preference"}:
        return False, "unknown_scope"
    item = MemoryReflectionInput(
        session_id=str(record.get("sourceSessionId") or ""),
        agent_id=str(record.get("agentId") or "") if scope != "agent" else str(record.get("ownerId") or ""),
        agent_name=str(record.get("agentName") or ""),
        user_message="",
        assistant_text="",
        user_id=str(record.get("ownerId") or "default") if scope == "user" else "default",
        project_slug=str(record.get("projectSlug") or record.get("project_slug") or "default"),
        source_run_id=str(record.get("sourceRunId") or ""),
    )
    memory_file = _build_memory_file_tool_set(store or MarkdownMemoryStore(), item, scope)  # type: ignore[arg-type]
    applied = await _apply_operation_to_file(
        memory_file,
        operation=str(record.get("operation") or "append"),
        section=_safe_section(str(record.get("section") or ""), scope),  # type: ignore[arg-type]
        note=str(record.get("note") or ""),
        exact_text=str(record.get("exactText") or ""),
        replacement_text=str(record.get("replacementText") or ""),
        reason=str(record.get("reason") or ""),
    )
    if applied is None:
        return False, "duplicate_or_exact_text_not_found"
    return True, applied.path


def _build_memory_file_tool_set(
    store: MarkdownMemoryStore,
    item: MemoryReflectionInput,
    scope: MemoryScope,
) -> MemoryFileToolSet:
    if scope == "global_preference":
        doc = store.read_global_memory()

        def read_content() -> str:
            return store.read_global_memory().content

        def write_content(content: str) -> None:
            store.write_global_memory(content)

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
    if not summary_items:
        items = [
            _strip_source_reference(line.strip())
            for line in sections.get("Summary", [])
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

