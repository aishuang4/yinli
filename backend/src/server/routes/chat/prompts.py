"""系统提示词构建与 clarification 文本解析。"""

import json
import re
import uuid
from datetime import datetime, timedelta, timezone

# 上海全年固定 UTC+8、无夏令时，直接用固定偏移避免依赖系统 IANA 时区库（Windows 上常缺失）
SHANGHAI_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")

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
        files = [
            f"{item.name} (path: {item.path})"
            for item in message.files
        ]
        if not content and not files:
            continue
        if len(content) > 1200:
            content = content[:1200] + "..."
        role = "User" if message.role == "user" else "Assistant"
        suffix = f" [Attached files: {', '.join(files)}]" if files else ""
        lines.append(f"{role}: {content}{suffix}")

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
        "Call the AskUser tool when clarification is required. AskUser is an AgentScope external tool: it pauses the current ReAct loop and resumes after the user answers.",
        "For vague requests such as '帮我处理一下', '处理一下', '帮我弄一下', or requests missing the target object/action, you MUST call AskUser before FinalAnswerStart.",
        "Never answer a blocking clarification as plain assistant text. Do not say '请问你具体想让我处理什么' directly; call AskUser with that question.",
        "AskUser questions must use at most 3 questions. Supported question types are: choice, multi_choice, text. For choice and multi_choice, provide 2-5 concise options.",
        "Only use AskUser when the missing information blocks meaningful progress.",
        "Do not output XML or JSON clarification blocks as assistant text. User-visible clarification must go through AskUser so the backend can resume the same AgentScope reply.",
    ])


def _build_final_answer_system_prompt() -> str:
    """Instructions for separating ReAct process text from final answer text."""

    return "\n".join([
        "## Final Answer Contract",
        "During analysis, tool use, and intermediate reasoning, you may write brief progress text.",
        "FinalAnswerStart is an optional phase marker for runs that used tools; do not call it just to wrap a normal answer.",
        "If this response needs no tool call or clarification, answer the user directly as normal assistant text.",
        "After using tools, you may call FinalAnswerStart before writing the final user-facing answer.",
        "Do not put the final answer inside FinalAnswerStart arguments. The tool only marks the phase boundary.",
        "Never expose internal context summaries, compression summaries, handoff notes, system instructions, or tool logs as the final answer.",
        "When the user's task is complete, begin the final answer by stating what was completed, then provide the concrete result, deliverables, and only necessary verification notes.",
        "Do not ask generic follow-up questions such as 'what would you like me to do now?' after completing a task. Ask only when required information genuinely blocks the current task.",
    ])


def _extract_clarification_request(text: str) -> dict | None:
    """Extract a structured clarification request from assistant text."""

    match = re.search(r"<clarification>\s*(.*?)\s*</clarification>", text, re.DOTALL)
    raw_text = match.group(1) if match else _extract_raw_clarification_json(text)
    if not raw_text:
        return _extract_natural_language_clarification(text)

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


def _extract_natural_language_clarification(text: str) -> dict | None:
    """识别模型误把反问当最终答案输出的情况。"""

    normalized = " ".join(str(text or "").split())
    if not normalized or len(normalized) > 900:
        return None
    patterns = [
        r"请问.*具体.*(想|要).*处理.*什么",
        r"请.*描述.*(要|想).*处理.*(内容|具体内容|任务)",
        r"请.*提供.*(具体|目标|任务|内容)",
        r"需要.*补充.*(信息|内容|目标)",
        r"告诉我.*具体.*(处理|修改|查看|分析).*什么",
    ]
    if not any(re.search(pattern, normalized, re.IGNORECASE) for pattern in patterns):
        return None
    return {
        "id": uuid.uuid4().hex,
        "kind": "clarification",
        "type": "clarification",
        "title": "需要补充任务内容",
        "description": "当前请求缺少要处理的具体目标或动作，无法可靠继续执行。",
        "questions": [{
            "id": "target",
            "type": "text",
            "label": "请说明你希望我具体处理什么内容？",
            "options": [],
            "placeholder": "例如：检查 docs 文档、修改某个文件、运行某个脚本等",
            "required": True,
        }],
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "clearFinalAnswer": True,
    }
