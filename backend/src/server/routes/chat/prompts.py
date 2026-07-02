"""系统提示词构建，以及 clarification / worker-selection 文本解析。"""

import json
import re
import uuid
from datetime import datetime, timedelta, timezone

# 上海全年固定 UTC+8、无夏令时，直接用固定偏移避免依赖系统 IANA 时区库（Windows 上常缺失）
SHANGHAI_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")

from src.server.routes.chat.models import ChatTeamMemberPayload
from src.server.routes.chat.store import _load_store


def _build_conversation_history_prompt(session_id: str | None, limit: int = 12) -> str:
    if not session_id:
        return ""

    store = _load_store()
    messages = store.messages.get(session_id, [])
    if not messages:
        return ""

    recent = messages[-limit:]
    lines = [
        "## Conversation History",
        "The following are recent messages from this session. Use them for continuity, but prioritize the latest user request.",
    ]
    for message in recent:
        content = " ".join((message.content or "").split())
        if not content:
            continue
        if len(content) > 1200:
            content = content[:1200] + "..."
        role = "User" if message.role == "user" else "Assistant"
        lines.append(f"{role}: {content}")

    return "\n".join(lines) if len(lines) > 2 else ""


def _build_runtime_context_prompt() -> str:
    """Build concrete runtime context that should be stable for every model call."""

    shanghai_now = datetime.now(SHANGHAI_TZ)
    utc_now = datetime.now(timezone.utc)
    return "\n".join(
        [
            "## Runtime Context",
            f"Current date/time: {shanghai_now.strftime('%Y-%m-%d %H:%M:%S %Z')} (Asia/Shanghai).",
            f"Current UTC time: {utc_now.strftime('%Y-%m-%d %H:%M:%S UTC')}.",
            'Treat the Asia/Shanghai date above as "today" unless the user specifies another timezone.',
            "For current-events or web-search tasks, include the current year/date when useful, prefer recent sources, and verify freshness before answering.",
        ]
    )


def _build_clarification_system_prompt() -> str:
    """Instructions for asking the user for missing information."""

    return "\n".join([
        "## Human Input / Clarification Contract",
        "If the user's request cannot be executed reliably because required information is missing, ask for clarification instead of guessing.",
        "Prefer calling the AskUser tool when clarification is required. AskUser pauses the current AgentScope ReAct loop and resumes after the user answers.",
        "AskUser questions must use at most 3 questions. Supported question types are: choice, multi_choice, text. For choice and multi_choice, provide 2-5 concise options.",
        "Only use AskUser when the missing information blocks meaningful progress.",
        "",
        "Legacy fallback only: if tools are unavailable and clarification is required, the first output token must be <clarification>. Output only one clarification block and no other text before or after it:",
        '<clarification>{"title":"需要补充信息","description":"简短说明为什么需要这些信息","questions":[{"id":"target","type":"choice","label":"请选择目标","options":["选项 A","选项 B"]},{"id":"needs","type":"multi_choice","label":"请选择需要的能力","options":["能力 A","能力 B"]},{"id":"details","type":"text","label":"请补充具体要求","placeholder":"输入补充信息"}]}</clarification>',
    ])


def _build_final_answer_system_prompt() -> str:
    """Instructions for separating ReAct process text from final answer text."""

    return "\n".join([
        "## Final Answer Contract",
        "During analysis, tool use, and intermediate reasoning, you may write brief progress text.",
        "When you are ready to provide the final user-facing answer, you MUST first call the FinalAnswerStart tool exactly once.",
        "Do not put the final answer inside FinalAnswerStart arguments. After the tool call succeeds, continue writing the final answer as normal assistant text.",
        "Do not call FinalAnswerStart before required tool calls, clarification, or checks are complete.",
    ])


def _extract_clarification_request(text: str) -> dict | None:
    """Extract a structured clarification request from assistant text."""

    match = re.search(r"<clarification>\s*(.*?)\s*</clarification>", text, re.DOTALL)
    raw_text = match.group(1) if match else _extract_raw_clarification_json(text)
    if not raw_text:
        return None

    try:
        raw = json.loads(raw_text)
    except json.JSONDecodeError:
        return None

    questions: list[dict[str, object]] = []
    for index, item in enumerate(raw.get("questions") or []):
        if not isinstance(item, dict):
            continue
        question_type = str(item.get("type") or "").strip()
        if question_type not in {"choice", "multi_choice", "text"}:
            continue
        question_id = str(item.get("id") or f"q{index + 1}").strip()
        label = str(item.get("label") or item.get("question") or "").strip()
        if not question_id or not label:
            continue
        options = [
            str(option).strip()
            for option in (item.get("options") or [])
            if str(option).strip()
        ][:5]
        if question_type in {"choice", "multi_choice"} and len(options) < 2:
            continue
        questions.append({
            "id": question_id,
            "type": question_type,
            "label": label,
            "options": options,
            "placeholder": str(item.get("placeholder") or "").strip(),
            "required": bool(item.get("required", True)),
        })

    if not questions:
        return None

    return {
        "id": uuid.uuid4().hex,
        "title": str(raw.get("title") or "需要补充信息").strip(),
        "description": str(raw.get("description") or "").strip(),
        "questions": questions[:3],
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }


def _extract_raw_clarification_json(text: str) -> str | None:
    """Extract a bare clarification JSON object when the model omits tags."""

    if '"questions"' not in text or '"title"' not in text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    return text[start:end + 1]


def _parse_worker_selection(text: str, allowed_agent_ids: set[str]) -> tuple[list[str], str]:
    """Parse model JSON for worker selection and filter unknown ids."""

    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?", "", raw, flags=re.IGNORECASE).strip()
        raw = re.sub(r"```$", "", raw).strip()
    if "{" in raw and "}" in raw:
        raw = raw[raw.find("{"): raw.rfind("}") + 1]

    try:
        data = json.loads(raw)
    except Exception:
        return [], "模型未返回有效选择，保持人工选择。"

    raw_ids = data.get("selectedAgentIds") or data.get("selected_agent_ids") or []
    selected: list[str] = []
    if isinstance(raw_ids, list):
        for item in raw_ids:
            agent_id = str(item).strip()
            if agent_id in allowed_agent_ids and agent_id not in selected:
                selected.append(agent_id)

    reason = str(data.get("reason") or "").strip()
    return selected, reason


def _build_team_system_prompt(
    team_name: str | None,
    members: list[ChatTeamMemberPayload],
) -> str:
    """Build Team context for the leader Agent."""
    if not team_name and not members:
        return ""

    lines = [
        "## Team Runtime Context",
        f"You are acting as the leader of Team: {team_name or 'Unnamed Team'}.",
        "Coordinate the worker agents according to their roles.",
        "Worker agents are executed in independent ReAct loops before your final response. Use their outputs as grounded team context and produce the final leader response.",
        "Available team members:",
    ]
    for member in members:
        dependency_note = (
            f" depends on {', '.join(member.depends_on)}"
            if member.role != "leader" and member.depends_on
            else ""
        )
        lines.append(
            f"- {member.role}: {member.name} ({member.agent_id})"
            + dependency_note
            + (f" - {member.description}" if member.description else "")
        )
    return "\n".join(lines)


def _build_worker_system_prompt(
    team_name: str | None,
    leader_name: str | None,
    member: ChatTeamMemberPayload,
) -> str:
    """Build AgentScope-style Team context for a worker Agent."""
    return "\n".join([
        "## AgentScope-style Team Worker Runtime Context",
        f"You are a worker agent in Team: {team_name or 'Unnamed Team'}.",
        f"Leader: {leader_name or 'Leader'}.",
        f"Your team role: {member.name}.",
        "Run your own isolated ReAct loop. Use tools when needed and stay within the shared workspace context.",
        "When your assignment is complete, your final response is treated as a TeamSay report to the leader.",
        "Report concrete findings, evidence, tool results, blockers, and confidence. Do not write the final team answer.",
    ])


def _build_worker_user_message(
    user_message: str,
    member: ChatTeamMemberPayload,
    dependency_outputs: list[dict[str, str]] | None = None,
    assignment: str = "",
    expected_output: str = "",
    constraints: str = "",
) -> str:
    """Build the delegated task sent to a worker."""
    assignment = assignment.strip() or build_worker_assignment_text(member)
    parts = [
        "Leader delegated this user request to you:",
        user_message,
        "Your assignment:",
        assignment,
    ]
    if expected_output.strip():
        parts.extend([
            "Expected output:",
            expected_output.strip(),
        ])
    if constraints.strip():
        parts.extend([
            "Constraints:",
            constraints.strip(),
        ])
    if dependency_outputs:
        parts.append("Upstream worker outputs you must use:")
        for item in dependency_outputs:
            parts.append(f"### {item.get('name') or item.get('agentId')}")
            parts.append(item.get("output") or "(No output)")
    parts.append("Return a TeamSay-style report to the leader. Do not write the final team answer; the leader will synthesize it.")
    return "\n\n".join(parts)


def build_worker_assignment_text(member: ChatTeamMemberPayload) -> str:
    """Build a member-specific assignment used by both UI and worker prompt."""

    handoff = member.handoff_prompt.strip()
    if handoff:
        return handoff

    description = member.description.strip()
    if description and description.lower() not in {"team worker session", "worker"}:
        return description

    name = member.name.strip() or "Worker"
    lowered = name.lower()
    if "调研" in name or "research" in lowered:
        return "负责梳理用户视角流程、现状信息和关键事实，给 Leader 提供调研结论。"
    if "架构" in name or "architect" in lowered:
        return "负责检查系统架构、模块边界、事件链路和长期可维护性风险。"
    if "开发" in name or "代码" in name or "developer" in lowered or "code" in lowered:
        return "负责检查后端/前端实现链路、关键代码路径、运行状态和可落地修改建议。"
    if "审查" in name or "review" in lowered:
        return "负责从缺陷、回归风险、验证漏洞和测试缺口角度进行审查。"
    if "测试" in name or "test" in lowered:
        return "负责设计验证步骤、测试案例和验收标准，并指出未覆盖风险。"
    if "文档" in name or "报告" in name or "doc" in lowered or "writer" in lowered:
        return "负责整理结构化结论、输出报告草稿和可交付表达。"
    if "知识" in name or "检索" in name or "search" in lowered:
        return "负责检索相关知识和证据，并返回可引用的材料摘要。"

    return f"作为 {name}，从你的专业角色出发分析用户请求，返回可供 Leader 汇总的明确发现。"


def _build_worker_outputs_system_prompt(worker_outputs: list[dict[str, str]]) -> str:
    """Build a leader-visible summary of actual worker reports."""
    lines = [
        "## Worker TeamSay Reports",
        "The following formal worker reports are authoritative context for your final team response.",
        "You must synthesize completed worker reports explicitly. If a worker failed or is waiting, say what data is missing instead of inventing details.",
    ]
    for item in worker_outputs:
        status = item.get("status") or "unknown"
        detail = item.get("detail") or ""
        review_status = item.get("reportReviewStatus") or ""
        review_notes = item.get("reportReviewNotes") or ""
        review_gaps = item.get("reportReviewGaps") or ""
        lines.append(f"### {item['name']} ({status})")
        if detail:
            lines.append(f"Status detail: {detail}")
        if review_status:
            lines.append(f"Report review: {review_status}")
        if review_notes:
            lines.append(f"Review notes: {review_notes}")
        if review_gaps:
            lines.append(f"Review gaps:\n{review_gaps}")
        lines.append(item.get("report") or item.get("output") or "(No report)")
    return "\n\n".join(lines)


def _build_leader_user_message(user_message: str, worker_outputs: list[dict[str, str]]) -> str:
    """Build the final leader task with worker outputs in the user-visible turn."""
    if not worker_outputs:
        return user_message

    return "\n\n".join([
        "Original user request:",
        user_message,
        _build_worker_outputs_system_prompt(worker_outputs),
        "Leader instruction:",
        "Produce the final answer based on the worker outputs above. Mention the useful worker findings and do not ignore them.",
    ])
