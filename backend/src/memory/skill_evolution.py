"""Skill 自进化的路由门禁与工作流证据摘要。"""

from __future__ import annotations

import json
import re
from hashlib import sha256
from typing import Any, Literal

from pydantic import BaseModel, Field

from src.storage.database import mark_skill_evolution_record_registered, upsert_skill_evolution_record


SkillGateRisk = Literal["low", "medium", "high", "skip"]
SkillEvolutionRecordStatus = Literal["draft", "registered", "skipped", "failed"]

_REUSABLE_MARKERS = (
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
    "迁移",
    "排查",
)
_COMPLETION_MARKERS = ("完成", "已", "done", "implemented", "added", "通过", "测试", "修复")
_SENSITIVE_KEY_RE = re.compile(r"(?i)(api[_-]?key|token|password|secret|cookie|private[_-]?key)")
_SECRET_VALUE_RE = re.compile(r"(?i)(sk-[a-z0-9_-]{12,}|token\s*[:=]\s*\S+|password\s*[:=]\s*\S+)")
_PATH_RE = re.compile(r"(?<![\w./-])[\w./-]+\.(?:py|ts|tsx|js|jsx|md|json|yml|yaml|toml|css|html|sql|sh)\b")
_TEST_RE = re.compile(r"\b(uv run pytest|pytest|npm run (?:test|build|lint)|pnpm (?:test|build|lint)|yarn (?:test|build|lint))\b")
_ID_KEY_RE = re.compile(r"(?i)(^id$|_id$|session|message|run|workspace|project|path|url|uri|token)")
_UNSAFE_SKILL_CONTENT_RE = re.compile(
    r"(?i)(api[_-]?key|password|secret|token|-----BEGIN [A-Z ]*PRIVATE KEY-----|sk-[a-z0-9_-]{12,})"
)
_SKILL_SIMILARITY_STOPWORDS = {
    "skill",
    "skills",
    "custom",
    "自动",
    "生成",
    "技能",
    "流程",
    "步骤",
    "使用",
    "用户",
    "需要",
    "执行",
    "进行",
    "时候",
    "一个",
    "这个",
    "根据",
    "输出",
    "the",
    "and",
    "for",
    "use",
    "uses",
    "using",
    "when",
    "with",
    "from",
}
_SKILL_CONCEPT_RE = re.compile(
    r"(?i)[a-z][a-z0-9]+_[a-z0-9_]+|"
    r"user_id|message_id|session_id|run_id|"
    r"查询|查找|搜索|发送|邮件|通知|用户|客户|检查|文档|参数|错误码|示例|"
    r"记忆|摘要|提炼|导出|导入|注册|安装|测试|构建|部署"
)


class SkillEvolutionGateDecision(BaseModel):
    """Skill 自进化 router gate 的规则判断结果。"""

    should_evaluate: bool = Field(alias="shouldEvaluate")
    reason: str
    risk: SkillGateRisk = "skip"
    signals: list[str] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class WorkflowVariableFlow(BaseModel):
    """工具调用之间的变量传递关系。"""

    source: str
    target: str
    variable: str


class WorkflowToolStep(BaseModel):
    """成功工具调用链路中的单步摘要。"""

    tool: str
    status: str
    purpose: str = ""
    input_summary: Any = Field(default_factory=dict, alias="inputSummary")
    output_variables: list[str] = Field(default_factory=list, alias="outputVariables")
    result_summary: str = Field(default="", alias="resultSummary")
    depends_on: list[str] = Field(default_factory=list, alias="dependsOn")

    model_config = {"populate_by_name": True}


class WorkflowFailureLesson(BaseModel):
    """有复用价值的失败调用摘要。"""

    tool: str
    failure_summary: str = Field(alias="failureSummary")
    lesson: str

    model_config = {"populate_by_name": True}


class DiscardedFailures(BaseModel):
    """被丢弃失败调用的数量与原因分类。"""

    count: int = 0
    reasons: list[str] = Field(default_factory=list)


class WorkflowEvidence(BaseModel):
    """提供给 SkillEvolutionAgent 的摘要化工作流证据。"""

    successful_tool_chain: list[WorkflowToolStep] = Field(default_factory=list, alias="successfulToolChain")
    useful_failures: list[WorkflowFailureLesson] = Field(default_factory=list, alias="usefulFailures")
    discarded_failures: DiscardedFailures = Field(default_factory=DiscardedFailures, alias="discardedFailures")
    changed_files: list[str] = Field(default_factory=list, alias="changedFiles")
    tests: list[str] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)
    variable_flow: list[WorkflowVariableFlow] = Field(default_factory=list, alias="variableFlow")
    tool_call_count: int = Field(default=0, alias="toolCallCount")

    model_config = {"populate_by_name": True}


class SkillEvolutionRecordInput(BaseModel):
    """持久化 Skill 自进化候选所需输入。"""

    session_id: str
    agent_id: str
    agent_name: str = ""
    user_message: str = ""
    assistant_text: str = ""
    user_id: str = "default"
    workspace_id: str = "default"
    project_id: str = "default"
    team_id: str = ""
    source_run_id: str = ""
    source_message_id: str = ""
    router_gate: SkillEvolutionGateDecision
    workflow_evidence: WorkflowEvidence
    candidate: Any | None = None
    status: SkillEvolutionRecordStatus = "skipped"
    risk_notes: str = ""


def is_accepted_skill_candidate(candidate: Any | None) -> bool:
    """判断 SkillEvolutionAgent 输出是否能进入草稿队列。"""

    if candidate is None:
        return False
    return bool(
        getattr(candidate, "reusable", False)
        and float(getattr(candidate, "confidence", 0) or 0) >= 0.72
        and getattr(candidate, "granularity", "") == "atomic"
        and not bool(getattr(candidate, "requires_workflow", False))
        and getattr(candidate, "draft", None) is not None
    )


def rejected_skill_candidate_reason(candidate: Any | None) -> str:
    """给被丢弃的 Skill 候选生成中文原因。"""

    if candidate is None:
        return "SkillEvolutionAgent 未返回可用候选。"
    if not getattr(candidate, "reusable", False):
        return "候选被模型判断为不可复用。"
    if float(getattr(candidate, "confidence", 0) or 0) < 0.72:
        return "候选置信度低于自动沉淀阈值。"
    if getattr(candidate, "granularity", "") != "atomic":
        return "候选不是原子化 Skill，应拆分或跳过。"
    if bool(getattr(candidate, "requires_workflow", False)):
        return "候选需要 Workflow 编排，暂不作为单个 Skill 草稿。"
    if getattr(candidate, "draft", None) is None:
        return "候选缺少标准 Skill 草稿内容。"
    return "候选未通过 Skill 自进化后端校验。"


def persist_skill_evolution_record(payload: SkillEvolutionRecordInput) -> dict[str, Any]:
    """把 gate、workflow evidence 和候选结果写入 Skill 自进化记录表。"""

    candidate = payload.candidate
    draft = getattr(candidate, "draft", None)
    record_id = _skill_evolution_record_id(payload)
    risk_notes = payload.risk_notes or (
        "" if payload.status == "draft" else rejected_skill_candidate_reason(candidate)
    )
    return upsert_skill_evolution_record({
        "id": record_id,
        "workspace_id": payload.workspace_id,
        "project_id": payload.project_id,
        "agent_id": payload.agent_id,
        "agent_name": payload.agent_name,
        "team_id": payload.team_id,
        "user_id": payload.user_id,
        "source_session_id": payload.session_id,
        "source_run_id": payload.source_run_id,
        "source_message_id": payload.source_message_id,
        "router_gate": payload.router_gate.model_dump(mode="json", by_alias=True),
        "workflow_evidence": payload.workflow_evidence.model_dump(mode="json", by_alias=True),
        "reusable": bool(getattr(candidate, "reusable", False)),
        "reusable_reason": str(getattr(candidate, "reason", "") or payload.router_gate.reason)[:500],
        "trigger_description": str(getattr(draft, "trigger", "") if draft else "")[:300],
        "inputs_schema": _infer_inputs_schema(payload.workflow_evidence),
        "draft_skill_name": str(getattr(draft, "name", "") if draft else "")[:120],
        "draft_skill_description": str(getattr(draft, "description", "") if draft else "")[:500],
        "draft_skill_content": str(getattr(draft, "content", "") if draft else ""),
        "draft_tags": list(getattr(draft, "tags", []) if draft else []),
        "granularity": str(getattr(candidate, "granularity", "") or ""),
        "requires_workflow": bool(getattr(candidate, "requires_workflow", False)),
        "suggested_split": [
            _model_dump_jsonish(item)
            for item in list(getattr(candidate, "suggested_split", []) or [])
        ],
        "variable_flow": _candidate_or_evidence_variable_flow(candidate, payload.workflow_evidence),
        "risk_notes": risk_notes[:800],
        "confidence": float(getattr(candidate, "confidence", 0) or 0),
        "status": payload.status,
    })


def register_skill_evolution_record(record: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    """把合格的 Skill 自进化草稿注册为标准 Skill。

    只注册到 Skills 系统，不会自动绑定到任何 Agent。
    """

    if record.get("status") == "registered":
        return record, "already_registered"
    if record.get("status") != "draft":
        return None, "record_not_draft"
    reason = _skill_record_registration_block_reason(record)
    if reason:
        if reason.startswith("similar_skill_exists:"):
            existing_skill_id = reason.split(":", 1)[1]
            updated = mark_skill_evolution_record_registered(
                str(record["id"]),
                registered_skill_id=existing_skill_id,
            )
            return updated, f"merged_similar_skill:{existing_skill_id}"
        return None, reason

    from src.server.routes.skills import SkillRegisterRequest, register_user_skill

    # 草稿字段由模型生成，长度不受控；按注册请求上限裁剪，避免 ValidationError 中断注册。
    skill = register_user_skill(
        SkillRegisterRequest(
            name=str(record.get("draftSkillName") or "").strip()[:80],
            description=str(record.get("draftSkillDescription") or "").strip()[:300],
            category="自进化",
            trigger=str(record.get("triggerDescription") or "").strip()[:200],
            content=str(record.get("draftSkillContent") or "").strip()[:12000],
            tags=[
                str(tag).strip()
                for tag in list(record.get("draftTags") or [])
                if str(tag).strip()
            ][:8],
        )
    )
    updated = mark_skill_evolution_record_registered(
        str(record["id"]),
        registered_skill_id=skill.id,
    )
    return updated, ""


def evaluate_skill_evolution_router_gate(
    *,
    user_message: str,
    assistant_text: str,
    tool_calls: list[Any] | tuple[Any, ...] = (),
    runtime_events: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
    run_status: str = "completed",
) -> SkillEvolutionGateDecision:
    """用保守规则判断本轮是否值得进入 Skill 自进化。"""

    user_text = _normalize_text(user_message)
    assistant = _normalize_text(assistant_text)
    if run_status != "completed":
        return SkillEvolutionGateDecision(
            shouldEvaluate=False,
            reason="本轮 Run 未成功完成，跳过 Skill 自进化。",
            risk="skip",
        )
    if not user_text or not assistant:
        return SkillEvolutionGateDecision(
            shouldEvaluate=False,
            reason="缺少用户目标或最终输出，跳过 Skill 自进化。",
            risk="skip",
        )
    if _contains_sensitive_content(user_text) or _contains_sensitive_content(assistant):
        return SkillEvolutionGateDecision(
            shouldEvaluate=False,
            reason="本轮包含疑似敏感信息，跳过 Skill 自进化。",
            risk="high",
            signals=["sensitive_content"],
        )

    evidence = build_workflow_evidence(tool_calls=tool_calls, runtime_events=runtime_events)
    text = f"{user_text}\n{assistant}".lower()
    signals: list[str] = []
    if any(marker in text for marker in _REUSABLE_MARKERS):
        signals.append("reusable_marker")
    if any(marker in text for marker in _COMPLETION_MARKERS):
        signals.append("completion_marker")
    if evidence.successful_tool_chain:
        signals.append("successful_tool_chain")
    if evidence.tests:
        signals.append("verification")
    if evidence.changed_files:
        signals.append("changed_files")
    if len(assistant) >= 240:
        signals.append("substantial_final_output")

    if "reusable_marker" not in signals:
        return SkillEvolutionGateDecision(
            shouldEvaluate=False,
            reason="本轮没有明显可复用工作信号，跳过 Skill 自进化。",
            risk="skip",
            signals=signals,
        )
    has_process_evidence = bool({"successful_tool_chain", "verification", "changed_files"} & set(signals))
    has_text_evidence = "substantial_final_output" in signals and "completion_marker" in signals
    if not (has_process_evidence or has_text_evidence):
        return SkillEvolutionGateDecision(
            shouldEvaluate=False,
            reason="缺少过程证据或完成证据，跳过 Skill 自进化。",
            risk="medium",
            signals=signals,
        )

    return SkillEvolutionGateDecision(
        shouldEvaluate=True,
        reason="本轮具备可复用工作信号和完成证据，可以进入 Skill 自进化。",
        risk="low" if has_process_evidence else "medium",
        signals=signals,
    )


def build_workflow_evidence(
    *,
    tool_calls: list[Any] | tuple[Any, ...] = (),
    runtime_events: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
) -> WorkflowEvidence:
    """从工具调用和运行事件构建摘要化 workflow evidence。"""

    tools = [_normalize_tool_call(item) for item in tool_calls]
    changed_files = _unique_limited(_extract_paths_from_text(_json_dump_safe(tools)), 24)
    tests = _unique_limited(_extract_tests_from_text(_json_dump_safe(tools)), 12)
    artifacts = _unique_limited([path for path in changed_files if _looks_like_artifact(path)], 12)
    successful: list[WorkflowToolStep] = []
    useful_failures: list[WorkflowFailureLesson] = []
    discarded_reasons: list[str] = []
    variable_flow: list[WorkflowVariableFlow] = []
    previous_outputs: list[tuple[str, str, Any]] = []

    for index, tool in enumerate(tools, start=1):
        name = str(tool.get("name") or f"tool_{index}")
        status = str(tool.get("status") or "")
        args = tool.get("args")
        result = tool.get("result")
        parsed_args = _parse_jsonish(args)
        parsed_result = _parse_jsonish(result)
        if _is_success_status(status):
            depends_on = _detect_depends_on(name, parsed_args, previous_outputs, variable_flow)
            output_variables = _extract_output_variables(parsed_result)
            successful.append(
                WorkflowToolStep(
                    tool=name,
                    status=status or "completed",
                    purpose=f"调用 {name} 完成本轮工作中的一个步骤。",
                    inputSummary=_redact_and_parameterize(parsed_args),
                    outputVariables=output_variables,
                    resultSummary=_summarize_result(result),
                    dependsOn=depends_on,
                )
            )
            for variable in output_variables:
                value = _lookup_value(parsed_result, variable)
                if value not in (None, ""):
                    previous_outputs.append((name, variable, value))
            continue

        if _is_failure_status(status):
            summary = _summarize_result(result)
            if _is_useful_failure(name, summary):
                useful_failures.append(
                    WorkflowFailureLesson(
                        tool=name,
                        failureSummary=summary or f"{name} 调用失败。",
                        lesson=f"未来复用时注意规避 {name} 暴露的失败条件。",
                    )
                )
            else:
                discarded_reasons.append("低复用价值失败或临时环境噪音")

    discarded = DiscardedFailures(
        count=len(discarded_reasons),
        reasons=_unique_limited(discarded_reasons, 8),
    )
    event_text = _json_dump_safe(list(runtime_events))
    if event_text:
        changed_files = _unique_limited(changed_files + _extract_paths_from_text(event_text), 24)
        tests = _unique_limited(tests + _extract_tests_from_text(event_text), 12)

    return WorkflowEvidence(
        successfulToolChain=successful[:24],
        usefulFailures=useful_failures[:8],
        discardedFailures=discarded,
        changedFiles=changed_files,
        tests=tests,
        artifacts=artifacts,
        variableFlow=variable_flow[:16],
        toolCallCount=len(tools),
    )


def _normalize_tool_call(item: Any) -> dict[str, Any]:
    if hasattr(item, "model_dump"):
        data = item.model_dump(mode="json")
    elif isinstance(item, dict):
        data = dict(item)
    else:
        data = {
            "id": getattr(item, "id", ""),
            "name": getattr(item, "name", ""),
            "args": getattr(item, "args", ""),
            "result": getattr(item, "result", ""),
            "status": getattr(item, "status", ""),
        }
    return {
        "id": str(data.get("id") or ""),
        "name": str(data.get("name") or data.get("tool") or ""),
        "args": data.get("args") or "",
        "result": data.get("result") or "",
        "status": str(data.get("status") or ""),
    }


def _normalize_text(value: str) -> str:
    return " ".join(str(value or "").split())


def _contains_sensitive_content(text: str) -> bool:
    return bool(_SENSITIVE_KEY_RE.search(text) or _SECRET_VALUE_RE.search(text))


def _is_success_status(status: str) -> bool:
    return status.lower() in {"completed", "complete", "success", "succeeded", "done"}


def _is_failure_status(status: str) -> bool:
    return status.lower() in {"failed", "failure", "error", "cancelled", "canceled", "timeout"}


def _parse_jsonish(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    text = str(value or "").strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except Exception:
        return text[:800]


def _redact_and_parameterize(value: Any, key: str = "") -> Any:
    if isinstance(value, dict):
        return {str(k): _redact_and_parameterize(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_and_parameterize(item, key) for item in value[:20]]
    if _SENSITIVE_KEY_RE.search(key):
        return "<redacted>"
    text = str(value)
    if _SECRET_VALUE_RE.search(text):
        return "<redacted>"
    if _ID_KEY_RE.search(key):
        return f"<{key}>"
    if "@" in text and "." in text:
        return "<email>"
    return text[:240]


def _extract_output_variables(value: Any) -> list[str]:
    variables: list[str] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            key_text = str(key)
            if _ID_KEY_RE.search(key_text):
                variables.append(key_text)
            variables.extend(_extract_output_variables(nested))
    elif isinstance(value, list):
        for item in value[:10]:
            variables.extend(_extract_output_variables(item))
    return _unique_limited(variables, 12)


def _lookup_value(value: Any, target_key: str) -> Any:
    if isinstance(value, dict):
        for key, nested in value.items():
            if str(key) == target_key:
                return nested
            found = _lookup_value(nested, target_key)
            if found is not None:
                return found
    if isinstance(value, list):
        for item in value:
            found = _lookup_value(item, target_key)
            if found is not None:
                return found
    return None


def _detect_depends_on(
    tool_name: str,
    args: Any,
    previous_outputs: list[tuple[str, str, Any]],
    variable_flow: list[WorkflowVariableFlow],
) -> list[str]:
    args_text = _json_dump_safe(args)
    depends_on: list[str] = []
    for source_tool, variable, value in previous_outputs:
        if value in (None, ""):
            continue
        if str(value) and str(value) in args_text:
            source = f"{source_tool}.{variable}"
            target_key = _find_key_for_value(args, value) or variable
            target = f"{tool_name}.{target_key}"
            depends_on.append(source)
            variable_flow.append(WorkflowVariableFlow(source=source, target=target, variable=variable))
    return _unique_limited(depends_on, 8)


def _find_key_for_value(value: Any, target_value: Any) -> str:
    if isinstance(value, dict):
        for key, nested in value.items():
            if nested == target_value:
                return str(key)
            found = _find_key_for_value(nested, target_value)
            if found:
                return found
    if isinstance(value, list):
        for item in value:
            found = _find_key_for_value(item, target_value)
            if found:
                return found
    return ""


def _summarize_result(value: Any) -> str:
    text = _normalize_text(str(value or ""))
    if not text:
        return ""
    text = _SECRET_VALUE_RE.sub("<redacted>", text)
    return text[:360]


def _is_useful_failure(tool_name: str, summary: str) -> bool:
    text = f"{tool_name}\n{summary}".lower()
    return any(marker in text for marker in ("permission", "not found", "unsupported", "invalid", "timeout", "缺少", "不存在", "不支持", "失败"))


def _extract_paths_from_text(text: str) -> list[str]:
    return _PATH_RE.findall(text or "")


def _extract_tests_from_text(text: str) -> list[str]:
    return [match.group(0) for match in _TEST_RE.finditer(text or "")]


def _looks_like_artifact(path: str) -> bool:
    return path.endswith((".pdf", ".docx", ".xlsx", ".pptx", ".png", ".jpg", ".jpeg", ".zip"))


def _unique_limited(values: list[str], limit: int) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
        if len(result) >= limit:
            break
    return result


def _json_dump_safe(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return str(value)


def _skill_evolution_record_id(payload: SkillEvolutionRecordInput) -> str:
    source = payload.source_run_id or payload.source_message_id
    if source:
        return f"skill-evolution-{sha256(source.encode('utf-8')).hexdigest()[:20]}"
    text = "\n".join([
        payload.session_id,
        payload.agent_id,
        payload.user_message[:500],
        payload.assistant_text[:500],
    ])
    return f"skill-evolution-{sha256(text.encode('utf-8')).hexdigest()[:20]}"


def _infer_inputs_schema(evidence: WorkflowEvidence) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    for flow in evidence.variable_flow:
        variable = str(flow.variable or "").strip()
        if not variable or variable in properties:
            continue
        properties[variable] = {
            "type": "string",
            "description": f"由 {flow.source} 传递到 {flow.target} 的运行时变量。",
        }
        required.append(variable)
    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }


def _candidate_or_evidence_variable_flow(candidate: Any | None, evidence: WorkflowEvidence) -> list[Any]:
    candidate_flow = [
        _model_dump_jsonish(item)
        for item in list(getattr(candidate, "variable_flow", []) or [])
    ]
    if candidate_flow:
        return candidate_flow
    return [
        item.model_dump(mode="json", by_alias=True)
        for item in evidence.variable_flow
    ]


def _model_dump_jsonish(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True)
    if isinstance(value, dict):
        return value
    return {"value": str(value)}


def _skill_record_registration_block_reason(record: dict[str, Any]) -> str:
    name = str(record.get("draftSkillName") or "").strip()
    content = str(record.get("draftSkillContent") or "").strip()
    if not name:
        return "missing_skill_name"
    if not content:
        return "missing_skill_content"
    if float(record.get("confidence") or 0) < 0.72:
        return "low_confidence"
    if record.get("granularity") != "atomic":
        return "not_atomic"
    if bool(record.get("requiresWorkflow")):
        return "requires_workflow"
    combined = "\n".join([
        name,
        str(record.get("draftSkillDescription") or ""),
        str(record.get("triggerDescription") or ""),
        content,
    ])
    if _UNSAFE_SKILL_CONTENT_RE.search(combined):
        return "unsafe_sensitive_content"
    duplicate = _find_similar_installed_skill(record)
    if duplicate:
        return f"similar_skill_exists:{duplicate}"
    return ""


def _find_similar_installed_skill(record: dict[str, Any]) -> str:
    try:
        from src.server.routes.skills import _skills
    except Exception:
        return ""

    record_signature = _skill_record_signature(record)
    name_slug = _simple_slug(record_signature["name"])
    for skill in _skills.values():
        if getattr(skill, "status", "") != "installed":
            continue
        skill_signature = _installed_skill_signature(skill)
        if _simple_slug(skill_signature["name"]) == name_slug:
            return skill.id
        if _is_similar_skill_signature(record_signature, skill_signature):
            return skill.id
    return ""


def _skill_record_signature(record: dict[str, Any]) -> dict[str, Any]:
    """把候选 Skill 归一化成相似度签名。"""

    return {
        "name": str(record.get("draftSkillName") or ""),
        "description": str(record.get("draftSkillDescription") or ""),
        "trigger": str(record.get("triggerDescription") or ""),
        "content": str(record.get("draftSkillContent") or ""),
        "tags": [str(tag) for tag in list(record.get("draftTags") or [])],
    }


def _installed_skill_signature(skill: Any) -> dict[str, Any]:
    """把已安装 Skill 归一化成相似度签名。"""

    return {
        "name": str(getattr(skill, "name", "") or ""),
        "description": str(getattr(skill, "description", "") or ""),
        "trigger": str(getattr(skill, "trigger", "") or ""),
        "content": str(getattr(skill, "content", "") or ""),
        "tags": [str(tag) for tag in list(getattr(skill, "tags", []) or [])],
    }


def _is_similar_skill_signature(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """判断两个 Skill 是否描述同一种可复用能力。"""

    left_core = _skill_core_tokens(left)
    right_core = _skill_core_tokens(right)
    if _jaccard_similarity(left_core, right_core) >= 0.62:
        return True
    if _containment_similarity(left_core, right_core) >= 0.84 and len(left_core & right_core) >= 5:
        return True

    left_trigger = _text_tokens(" ".join([left["description"], left["trigger"], " ".join(left["tags"])]))
    right_trigger = _text_tokens(" ".join([right["description"], right["trigger"], " ".join(right["tags"])]))
    trigger_overlap = _containment_similarity(left_trigger, right_trigger)
    left_content = _text_tokens(left["content"])
    right_content = _text_tokens(right["content"])
    content_overlap = _containment_similarity(left_content, right_content)
    if trigger_overlap >= 0.76 and content_overlap >= 0.56:
        return True
    if content_overlap >= 0.64 and len(left_content & right_content) >= 5:
        return True
    left_concepts = _concept_tokens(_skill_signature_text(left))
    right_concepts = _concept_tokens(_skill_signature_text(right))
    if _containment_similarity(left_concepts, right_concepts) >= 0.78 and len(left_concepts & right_concepts) >= 3:
        return True

    name_overlap = _containment_similarity(_text_tokens(left["name"]), _text_tokens(right["name"]))
    if name_overlap >= 0.75 and trigger_overlap >= 0.65:
        return True
    return False


def _skill_core_tokens(signature: dict[str, Any]) -> set[str]:
    weighted_text = " ".join([
        str(signature["name"]),
        str(signature["name"]),
        str(signature["description"]),
        str(signature["description"]),
        str(signature["trigger"]),
        str(signature["trigger"]),
        str(signature["content"]),
        " ".join(signature["tags"]),
        " ".join(signature["tags"]),
    ])
    return _text_tokens(weighted_text)


def _skill_signature_text(signature: dict[str, Any]) -> str:
    return " ".join([
        str(signature["name"]),
        str(signature["description"]),
        str(signature["trigger"]),
        str(signature["content"]),
        " ".join(signature["tags"]),
    ])


def _simple_slug(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", value.lower()).strip("-")


def _text_tokens(value: str) -> set[str]:
    normalized = value.lower()
    tokens: set[str] = set()
    for token in re.findall(r"[a-z0-9][a-z0-9_-]{1,}", normalized):
        cleaned = token.strip("_-")
        if cleaned and cleaned not in _SKILL_SIMILARITY_STOPWORDS:
            tokens.add(cleaned)
    for chunk in re.findall(r"[\u4e00-\u9fff]{2,}", normalized):
        if chunk not in _SKILL_SIMILARITY_STOPWORDS:
            tokens.add(chunk)
        for size in (2, 3):
            for index in range(0, max(len(chunk) - size + 1, 0)):
                gram = chunk[index:index + size]
                if gram not in _SKILL_SIMILARITY_STOPWORDS:
                    tokens.add(gram)
    return tokens


def _concept_tokens(value: str) -> set[str]:
    """提取 Skill 能力相似度的关键概念 token。"""

    normalized = value.lower()
    return {
        token.lower()
        for token in _SKILL_CONCEPT_RE.findall(normalized)
        if token and token.lower() not in _SKILL_SIMILARITY_STOPWORDS
    }


def _jaccard_similarity(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _containment_similarity(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    overlap = len(left & right)
    return max(overlap / len(left), overlap / len(right))
