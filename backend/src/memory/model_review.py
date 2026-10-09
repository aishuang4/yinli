"""可复用的模型审核门。

技能自进化是第一个调用方。审核只返回放行或拒绝，不写技能、不改记忆。
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from src.memory.admission import is_trivial_turn
from src.memory.sanitizer import contains_sensitive_content


ReviewSource = Literal["model", "rule", "fallback"]
_THINK_RE = re.compile(r"(?is)<think>.*?</think>")
_MESSAGE_TEXT_LIMIT = 2000
_SUMMARY_LIMIT = 8000
_TOOL_RESULT_LIMIT = 200
_PROMPT_VERSION = "skill-review-v1"

SKILL_REVIEW_CRITERIA = """\
回顧上面的對話，判斷這次工作要不要進入技能草稿生成。要積極。多數完成的工作如果出現了可重複做法或用戶糾正，就應該放行。什麼都不放行不是中性結果，除非這次確實沒有新手法、也沒有糾正。

技能是什麼：針對這一類任務、按這位使用者的要求，把事情做得又對又省事的說明。包括步驟、真正能用的工具和命令、順序、結果應該長什麼樣，以及會浪費時間的坑。以後照著做，應該第一次就能做出要的東西。
  • 要的是類別級做法，不是一次 session 的敘事。
  • 坑要寫成可推廣的規則加一句原因，不要複述這次發生了什麼。
  • 用戶糾正了風格、語氣、格式、囉嗦程度、步驟或工作流，這是一等信號。
  • 出現了以後還用得上的手法、修復、繞過辦法或工具用法，應該放行。
  • 這次用過的技能是錯的、缺步驟或過時了，應該放行。
  • 同一個教訓不要變成兩份技能。

不要放行的：
  • 缺命令、剛安裝失敗、路徑遷移、憑證沒配、套件沒裝。使用者可以修好，這不是持久規則。
  • 「某工具壞了」這類負面斷言。
  • 結束前已經用重試解決的瞬時錯誤。教訓若有，也只是重試方式。
  • 一次性任務，例如總結今天的市場、分析某一個 PR。
  • 試了幾樣都不行、最後叫使用者自己查。不要把沒跑通的嘗試當成可靠流程。

如果這次很順利、沒有糾正、也沒有新手法，就拒絕。否則放行。
不要寫技能檔，不要呼叫任何工具。
只返回一個 JSON 對象：{"allow": true 或 false, "confidence": 0 到 1 之間的小數, "reason": "中文理由"}。
"""


class ModelReviewError(RuntimeError):
    """模型审核没有给出可解析的判定。"""


@dataclass(frozen=True)
class ModelReviewRequest:
    """一次语义审核的输入。调用方不依赖记忆准入模块。"""

    purpose: str
    question: str
    evidence: dict[str, Any]
    allow_criteria: str
    deny_criteria: str


@dataclass(frozen=True)
class ModelReviewDecision:
    """审核判定。source 区分模型、确定性规则和规则回退。"""

    allow: bool
    reason: str
    confidence: float = 0.0
    source: ReviewSource = "model"
    signals: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReviewContextMessage:
    """压缩后仍留在上下文里的一条消息。"""

    role: str
    text: str


@dataclass(frozen=True)
class ActivatedSkillRef:
    """本轮用过的技能。只保留标识和名称。"""

    skill_id: str
    name: str


@dataclass(frozen=True)
class ReviewContextSnapshot:
    """成功收尾时从 Agent 状态拷走的审核上下文。"""

    summary: str = ""
    messages: tuple[ReviewContextMessage, ...] = ()
    activated_skills: tuple[ActivatedSkillRef, ...] = ()


Reviewer = Callable[[ModelReviewRequest], Awaitable[ModelReviewDecision]]


def build_skill_review_prompt(snapshot: ReviewContextSnapshot) -> str:
    """按摘要、近期原文、技能名称、判断标准组装审核上下文。"""

    parts: list[str] = []
    summary = _trim(snapshot.summary, _SUMMARY_LIMIT)
    if summary:
        parts.append(f"压缩摘要:\n{summary}")
    for message in snapshot.messages:
        text = _trim(_strip_think(message.text), _MESSAGE_TEXT_LIMIT)
        if text:
            parts.append(f"{message.role}:\n{text}")
    if snapshot.activated_skills:
        names = "\n".join(f"- {item.skill_id}: {item.name}" for item in snapshot.activated_skills)
        parts.append(f"本轮已完成技能（只有标识和名称）:\n{names}")
    parts.append(SKILL_REVIEW_CRITERIA)
    return "\n\n".join(parts)


def skill_review_request(snapshot: ReviewContextSnapshot) -> ModelReviewRequest:
    """技能自进化触发用的审核请求。"""

    return ModelReviewRequest(
        purpose="skill_evolution_trigger",
        question="这次已完成的工作要不要进入技能草稿生成？",
        evidence={
            "summary": snapshot.summary,
            "messages": [{"role": item.role, "text": item.text} for item in snapshot.messages],
            "activatedSkills": [
                {"id": item.skill_id, "name": item.name} for item in snapshot.activated_skills
            ],
        },
        allow_criteria="出现可重复做法或用户纠正",
        deny_criteria="没有新手法、没有纠正，或只是环境故障和未跑通的尝试",
    )


def parse_model_review_text(text: str) -> ModelReviewDecision:
    """从模型文本里取出放行判定。解析失败交给调用方回退。"""

    data = _extract_json_object(text)
    if "allow" not in data:
        raise ModelReviewError("模型审核没有返回 allow")
    try:
        confidence = float(data.get("confidence") or 0)
    except (TypeError, ValueError) as exc:
        raise ModelReviewError("模型审核的 confidence 无法解析") from exc
    return ModelReviewDecision(
        allow=bool(data.get("allow")),
        reason=str(data.get("reason") or "")[:500],
        confidence=min(max(confidence, 0.0), 1.0),
        source="model",
    )


async def review(
    request: ModelReviewRequest,
    *,
    reviewer: Reviewer,
) -> ModelReviewDecision:
    """执行一次无副作用判定。reviewer 由调用方注入，便于测试。"""

    return await reviewer(request)


def snapshot_review_context(
    agent: Any,
    skill_runs: list[Any] | None = None,
) -> ReviewContextSnapshot:
    """从当前 Agent 拷贝压缩摘要和仍留在上下文里的消息。"""

    state = getattr(agent, "state", None)
    summary = _render_summary(getattr(state, "summary", "") if state is not None else "")
    context = list(getattr(state, "context", []) or []) if state is not None else []
    messages = tuple(
        message
        for message in (_render_context_message(item) for item in context)
        if message is not None
    )
    return ReviewContextSnapshot(
        summary=summary,
        messages=messages,
        activated_skills=activated_skill_refs(skill_runs or []),
    )


def activated_skill_refs(skill_runs: list[Any]) -> tuple[ActivatedSkillRef, ...]:
    """只保留已完成技能的 id 和 name。"""

    refs: list[ActivatedSkillRef] = []
    for skill in skill_runs:
        if skill is None:
            continue
        if isinstance(skill, dict):
            status = str(skill.get("status") or "")
            skill_id = str(skill.get("id") or "").strip()
            name = str(skill.get("name") or "").strip()
        else:
            status = str(getattr(skill, "status", "") or "")
            skill_id = str(getattr(skill, "id", "") or "").strip()
            name = str(getattr(skill, "name", "") or "").strip()
        if status and status != "completed":
            continue
        if skill_id or name:
            refs.append(ActivatedSkillRef(skill_id=skill_id, name=name))
    return tuple(refs)


def turn_only_snapshot(
    *,
    user_message: str,
    assistant_text: str,
    skill_runs: list[Any] | None = None,
) -> ReviewContextSnapshot:
    """没有 Agent 上下文时，只用本轮用户句和最终回覆。"""

    messages = [
        ReviewContextMessage("user", user_message),
        ReviewContextMessage("assistant", assistant_text),
    ]
    return ReviewContextSnapshot(
        messages=tuple(item for item in messages if item.text.strip()),
        activated_skills=activated_skill_refs(skill_runs or []),
    )


def deterministic_skill_gate(
    *,
    user_message: str,
    assistant_text: str,
    manual_skill_request: bool = False,
    run_completed: bool = True,
) -> ModelReviewDecision | None:
    """模型之前的短路。返回 None 表示应该调用模型。"""

    if not run_completed:
        return _rule(False, "本轮 Run 未成功完成，跳过 Skill 自进化。")
    if not user_message.strip() or not assistant_text.strip():
        return _rule(False, "缺少用户目标或最终输出，跳过 Skill 自进化。")
    if contains_sensitive_content(user_message) or contains_sensitive_content(assistant_text):
        return _rule(False, "本轮包含疑似敏感信息，跳过 Skill 自进化。", signals=("sensitive_content",))
    if manual_skill_request:
        return _rule(True, "用户显式要求把前序完成工作生成 Skill。", signals=("manual_skill_request",))
    if is_trivial_turn(user_message, assistant_text):
        return _rule(False, "寒暄回合不进入 Skill 自进化。")
    return None


def _rule(
    allow: bool,
    reason: str,
    signals: tuple[str, ...] = (),
) -> ModelReviewDecision:
    return ModelReviewDecision(
        allow=allow,
        reason=reason,
        confidence=1.0,
        source="rule",
        signals=signals,
    )


def _render_summary(summary: Any) -> str:
    if isinstance(summary, str):
        return _strip_think(summary).strip()
    if isinstance(summary, list):
        return _strip_think("\n".join(_block_text(item) for item in summary)).strip()
    text = str(getattr(summary, "text", "") or "")
    return _strip_think(text).strip()


def _render_context_message(message: Any) -> ReviewContextMessage | None:
    role = str(getattr(message, "role", "") or "")
    if not role and isinstance(message, dict):
        role = str(message.get("role") or "")
    text = _strip_think(_message_body(message)).strip()
    if not role or not text:
        return None
    return ReviewContextMessage(role=role, text=text)


def _message_body(message: Any) -> str:
    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(piece for piece in (_block_text(block) for block in content) if piece)
    return _block_text(content)


def _block_text(block: Any) -> str:
    if block is None:
        return ""
    if isinstance(block, str):
        return block
    if isinstance(block, dict):
        block_type = str(block.get("type") or "")
        if block_type in {"thinking", "reasoning"}:
            return ""
        if block.get("text"):
            return str(block.get("text") or "")
        name = str(block.get("name") or "")
        output = block.get("output")
        if name or output:
            result = _trim(_render_summary(output) if not isinstance(output, str) else output, _TOOL_RESULT_LIMIT)
            return f"[tool {name}] {result}".strip()
        return ""
    block_type = str(getattr(block, "type", "") or "")
    if block_type in {"thinking", "reasoning"}:
        return ""
    text = getattr(block, "text", None)
    if text:
        return str(text)
    name = str(getattr(block, "name", "") or "")
    output = getattr(block, "output", None)
    if name or output is not None:
        rendered = output if isinstance(output, str) else _render_summary(output)
        result = _trim(rendered, _TOOL_RESULT_LIMIT)
        return f"[tool {name}] {result}".strip()
    return ""


def _strip_think(text: str) -> str:
    return _THINK_RE.sub("", text or "")


def _trim(text: str, limit: int) -> str:
    value = (text or "").strip()
    if len(value) <= limit:
        return value
    return value[:limit]


def _extract_json_object(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(raw[start : end + 1])
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}

