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
@dataclass(frozen=True)
class MemoryReflectionInput:
    """Markdown 记忆反思输入。"""

    session_id: str
    agent_id: str
    agent_name: str
    user_message: str
    assistant_text: str
    user_id: str = "default"
    project_slug: str = "default"
    team_id: str = ""
    source_run_id: str = ""
    source_message_id: str = ""
    tool_calls: list[object] = field(default_factory=list)
    runtime_events: list[dict] = field(default_factory=list)
    manual_skill_request: bool = False
    review_context: object | None = None


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
    # user / global 层进入待确认队列的候选（已落库记录）。
    candidates: list[dict] = field(default_factory=list)
    skill_candidate: SkillEvolutionAgentPayload | None = None
    skill_evolution_record: dict | None = None
    ignored_reason: str = ""
    admission: object | None = None


def reflect_completed_run_memory(*args, **kwargs):
    """规则提炼旧三层 Markdown。实现在 markdown_reflection，避免循环导入。"""

    from src.memory.markdown_reflection import reflect_completed_run_memory as impl

    return impl(*args, **kwargs)


def _build_memory_file_tool_set(*args, **kwargs):
    from src.memory.markdown_reflection import _build_memory_file_tool_set as impl

    return impl(*args, **kwargs)


async def _apply_memory_operations(*args, **kwargs):
    from src.memory.markdown_reflection import _apply_memory_operations as impl

    return await impl(*args, **kwargs)


async def apply_markdown_candidate(*args, **kwargs):
    """把待确认候选写进旧 Markdown。实现在 markdown_reflection。"""

    from src.memory.markdown_reflection import apply_markdown_candidate as impl

    return await impl(*args, **kwargs)


async def reflect_completed_run_memory_with_agents(
    item: MemoryReflectionInput,
    *,
    model_name: str,
    fallback_names: list[str] | None = None,
    store: MarkdownMemoryStore | None = None,
    rule_admission: object | None = None,
    include_markdown: bool = True,
) -> MemoryReflectionResult:
    """调用专用系统 Agent 提炼记忆和 Skill 候选，失败时使用规则兜底。

    `rule_admission` 是本轮已算好的规则准入结果（与历史节点持久化共用一份），
    这里只在其上叠加可选的模型评分，不再重新评一次。
    """

    user_message = _normalize_text(item.user_message)
    assistant_text = _normalize_text(item.assistant_text)
    admission = await _admission_for_item_with_model(
        item,
        user_message,
        assistant_text,
        model_name=model_name,
        fallback_names=fallback_names or [],
        rule_decision=rule_admission,
    )
    if admission.sensitive or admission.reason == "empty_turn":
        return MemoryReflectionResult(ignored_reason=admission.reason, admission=admission)

    memory_store = store or MarkdownMemoryStore()
    updates: list[MemoryMarkdownUpdate] = []
    candidates: list[dict] = []
    memory_reason = ""
    if include_markdown and admission.should_write_markdown:
        memory_files = {
            scope: _build_memory_file_tool_set(memory_store, item, scope) for scope in _all_memory_scopes()
        }
        # 一路模型调用同时提炼三层；模型不挂写工具，操作由这里按 scope 决定直接写还是进待确认队列。
        try:
            payload = await run_memory_reflection_agent(
                user_message=user_message,
                assistant_text=assistant_text,
                agent_id=item.agent_id,
                agent_name=item.agent_name,
                memory_files=memory_files,
                tool_trajectory=build_tool_trajectory(item.tool_calls),
                model_name=model_name,
                fallback_names=fallback_names or [],
                invocation_key=_reflection_invocation_key(item, "markdown"),
            )
        except Exception:
            payload = None
        if payload is not None:
            memory_reason = payload.reason
            if payload.should_write:
                applied_updates, applied_candidates = await _apply_memory_operations(
                    payload.memories,
                    memory_files=memory_files,
                    item=item,
                )
                updates.extend(applied_updates)
                candidates.extend(applied_candidates)
        else:
            # 模型不可用才用规则兜底；模型明确说"没有值得写的"不兜底。
            fallback = reflect_completed_run_memory(item, store=memory_store)
            updates.extend(fallback.updates)
            candidates.extend(fallback.candidates)
            memory_reason = memory_reason or fallback.ignored_reason

    skill_candidate = None
    skill_evolution_record = None
    gate = await _skill_evolution_gate_for_item(
        item=item,
        user_message=user_message,
        assistant_text=assistant_text,
        model_name=model_name,
        fallback_names=fallback_names or [],
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

    if updates or candidates or skill_candidate or skill_evolution_record:
        return MemoryReflectionResult(
            updates=updates,
            candidates=candidates,
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


async def _skill_evolution_gate_for_item(
    *,
    item: MemoryReflectionInput,
    user_message: str,
    assistant_text: str,
    model_name: str,
    fallback_names: list[str],
) -> SkillEvolutionGateDecision:
    """确定性短路之后，用模型审核决定是否进入技能草稿生成。"""

    from src.memory.model_review import (
        ModelReviewError,
        deterministic_skill_gate,
        review,
        skill_review_request,
        turn_only_snapshot,
    )
    from src.memory.model_review import build_skill_review_prompt

    short = deterministic_skill_gate(
        user_message=user_message,
        assistant_text=assistant_text,
        manual_skill_request=item.manual_skill_request,
    )
    if short is not None:
        return _gate_from_review(short)

    snapshot = item.review_context or turn_only_snapshot(
        user_message=user_message,
        assistant_text=assistant_text,
    )

    async def _reviewer(request):
        from src.memory.extraction_agents import run_model_review_agent
        from src.memory.model_review import parse_model_review_text

        del request
        text = await run_model_review_agent(
            user_prompt=build_skill_review_prompt(snapshot),
            model_name=model_name,
            fallback_names=fallback_names,
            invocation_key=_reflection_invocation_key(item, "model-review"),
        )
        return parse_model_review_text(text)

    try:
        decision = await review(skill_review_request(snapshot), reviewer=_reviewer)
    except (ModelReviewError, Exception):
        fallback = evaluate_skill_evolution_router_gate(
            user_message=user_message,
            assistant_text=assistant_text,
            tool_calls=item.tool_calls,
            runtime_events=item.runtime_events,
        )
        signals = [*fallback.signals, "source:fallback"]
        return fallback.model_copy(update={
            "reason": f"[fallback] {fallback.reason}",
            "signals": signals,
        })
    return _gate_from_review(decision)


def _gate_from_review(decision) -> SkillEvolutionGateDecision:
    """把审核判定映射成现有 router_gate 记录。"""

    signals = [f"source:{decision.source}", *list(getattr(decision, "signals", ()) or ())]
    return SkillEvolutionGateDecision(
        shouldEvaluate=decision.allow,
        reason=decision.reason,
        risk="low" if decision.allow else "skip",
        signals=signals,
    )


def evaluate_turn_admission(item: MemoryReflectionInput):
    """本轮规则准入：历史节点持久化与 Markdown 提炼共用同一份结果。"""

    return _admission_for_item(item, _normalize_text(item.user_message), _normalize_text(item.assistant_text))


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
    rule_decision: object | None = None,
):
    """规则准入 + 可选模型评分增强。"""

    decision = rule_decision or _admission_for_item(item, user_message, assistant_text)
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


def _all_memory_scopes() -> tuple[MemoryScope, MemoryScope, MemoryScope]:
    return ("user", "agent", "global_preference")


# 工具轨迹摘要：条数与单条字符上限。只给 agent 层提炼"哪条路径有效"用，不进记忆正文。
TOOL_TRAJECTORY_LIMIT = 12
TOOL_TRAJECTORY_ARGS_CHARS = 160
TOOL_TRAJECTORY_RESULT_CHARS = 200


def build_tool_trajectory(tool_calls: list[object] | tuple[object, ...], *, limit: int = TOOL_TRAJECTORY_LIMIT) -> list[dict]:
    """把本轮工具调用压成脱敏的短轨迹：工具名、状态、参数要点、结果开头。"""

    trajectory: list[dict] = []
    calls = list(tool_calls or [])
    # 超过上限时保留开头几步和最后几步：开头是探索路径，结尾是产出。
    if len(calls) > limit:
        head = limit // 2
        calls = calls[:head] + calls[-(limit - head):]
    for call in calls:
        name = str(getattr(call, "name", "") or (call.get("name") if isinstance(call, dict) else "") or "")
        status = str(getattr(call, "status", "") or (call.get("status") if isinstance(call, dict) else "") or "")
        args = str(getattr(call, "args", "") or (call.get("args") if isinstance(call, dict) else "") or "")
        result = str(getattr(call, "result", "") or (call.get("result") if isinstance(call, dict) else "") or "")
        if not name:
            continue
        entry = {
            "tool": name,
            "status": status or "completed",
            "args": _trim(sanitize_memory_note_text(args), TOOL_TRAJECTORY_ARGS_CHARS),
        }
        if result:
            entry["result"] = _trim(sanitize_memory_note_text(result), TOOL_TRAJECTORY_RESULT_CHARS)
        trajectory.append(entry)
    return trajectory


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
                project_slug=item.project_slug,
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


def _reflection_invocation_key(item: MemoryReflectionInput, target: str) -> str:
    raw = "|".join([
        item.session_id,
        item.source_run_id,
        item.source_message_id,
        item.agent_id,
        item.user_id,
        item.project_slug,
        target,
        item.user_message[:160],
    ])
    return sha256(raw.encode("utf-8")).hexdigest()[:16]


def _normalize_text(value: str) -> str:
    return " ".join(value.split())


def _trim(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + "..."
