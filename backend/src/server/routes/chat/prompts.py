"""系统提示词构建与 clarification 文本解析。"""

import json
import re
import uuid
from datetime import datetime, timedelta, timezone

from src.skills.invocation import skill_runtime_directory

# 上海全年固定 UTC+8、无夏令时，直接用固定偏移避免依赖系统 IANA 时区库（Windows 上常缺失）
SHANGHAI_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")


def _build_file_attachments_user_note(files) -> str:
    """本轮附件说明。

    附件是这一轮用户消息的事实，随用户消息进入 AgentScope 对话上下文，
    这样后续轮次模型仍知道之前上传过什么；不再写进 system prompt。
    """

    if not files:
        return ""
    lines = [
        "<attached_files>",
        "The user uploaded these files to the current Project. Read or modify them using the listed relative paths.",
    ]
    lines.extend(
        f"- {item.name}: `{item.path}` ({item.size} bytes, {item.media_type})"
        for item in files
    )
    lines.append("</attached_files>")
    return "\n".join(lines)


def build_stacked_skill_user_message(
    skills,
    user_instruction: str,
    *,
    guardrails: object | None = None,
) -> str:
    """把本輪選中的 Skill 拼成一條用戶消息。全文不進 system prompt。

    總頭在前，用戶指令寫在總頭裡；後面按選擇順序接每個 Skill 的正文塊。
    一個都沒載入成功時返回空字串，呼叫端繼續用用戶原文。
    """

    from src.skills.invocation import load_skill_invocations

    batch = load_skill_invocations(list(skills or []), guardrails=guardrails, strict=False)
    if not batch.loaded:
        return ""
    names = [item.name for item in batch.loaded]
    slash = " ".join(f"/{name}" for name in names)
    lines = [
        (
            f'[IMPORTANT: The user has invoked the "{slash}" stacked skill bundle, '
            f"loading {len(names)} skills together. Treat every skill below as active guidance for this turn.]"
        ),
        "",
        f"Skills loaded: {', '.join(names)}",
    ]
    if batch.missing:
        lines.append(f"Skills missing (skipped): {', '.join(batch.missing)}")
    if batch.disabled:
        lines.append(f"Skills disabled for this platform (skipped): {', '.join(batch.disabled)}")
    lines.extend(["", f"User instruction: {str(user_instruction or '').strip()}"])
    return "\n\n".join(["\n".join(lines), *(_skill_block(item) for item in batch.loaded)])


def _skill_block(item) -> str:
    """一個 Skill 的正文塊。附屬檔用絕對路徑，仍走現有 Read / Bash，不另寫 viewer。"""

    parts = [
        f'[Loaded as part of the stacked skill invocation "{item.name}".]',
        "",
        item.markdown.strip(),
        "",
        (
            f'The Skill tool only returns this SKILL.md. It is already loaded above; '
            f'do not call the Skill tool again for "{item.name}".'
        ),
    ]
    directory = str(item.directory or "")
    if directory:
        parts.extend([
            "",
            f"[Skill directory: {directory}]",
            (
                "Resolve any relative paths in this skill (e.g. `scripts/foo.js`, "
                "`templates/config.yaml`) against that directory, then run them "
                "with Bash using the absolute path."
            ),
        ])
    supporting = list(item.supporting_files or [])
    if supporting and directory:
        parts.extend(["", "[This skill has supporting files:]"])
        for relative in supporting:
            parts.append(f"- {relative}  ->  {directory.rstrip('/')}/{relative}")
        parts.append("Read these with the Read tool using the absolute path, or run scripts directly by absolute path.")
    return "\n".join(parts)


def compose_turn_user_text(*, message: str, skill_message: str = "", note: str = "") -> str:
    """有 Skill 總頭時，它就是這條用戶消息；附件說明仍接在後面。"""

    body = skill_message or message
    if note:
        return f"{body}\n\n{note}" if body else note
    return body


def _build_runtime_context_prompt() -> str:
    """Stable runtime-context rules. Must not contain anything that changes between calls.

    The concrete wall-clock time is injected by AgentScope as a
    ``<system-reminder>`` hint block (``<current-time>`` / ``<timezone>``)
    on the first reply and whenever enough time has elapsed, so the system
    prompt stays byte-identical across turns and provider prompt caching works.
    """

    return "\n".join(
        [
            "## Runtime Context",
            "The current date/time is provided in the conversation inside a <system-reminder> block as <current-time> with its <timezone> (Asia/Shanghai by default).",
            'Treat the most recent <current-time> as "today" unless the user specifies another timezone; earlier reminders are outdated.',
            "For current-events or web-search tasks, include the current year/date when useful, prefer recent sources, and verify freshness before answering.",
        ]
    )


def _build_scheduled_preloaded_skill_prompt(skills, *, guardrails: object | None = None) -> str:
    """定時任務只聲明 Skill 已注入，正文留在 `<activated-skill>`。"""

    items = list(skills or [])
    if not items:
        return ""
    lines = [
        "## Preloaded Skills",
        "Required Skill instructions are already in this conversation inside <activated-skill> blocks.",
        "Follow those blocks. This run does not provide a Skill viewer.",
        "Load references/ and scripts/ on demand with Read, Grep, Glob, or Bash.",
        "Resolve those relative paths against the Skill directory below.",
    ]
    for skill in items:
        name = str(getattr(skill, "name", "") or "").strip()
        directory = skill_runtime_directory(skill, guardrails)
        if name and directory:
            lines.append(f"- {name}: `{directory}`")
        elif name:
            lines.append(f"- {name}")
    return "\n".join(lines)


def _build_skill_context_prompt() -> str:
    """Stable rules for how Skill bodies live in the conversation.

    Bodies read through the ``Skill`` tool stay in context for the rest of the
    session and are re-attached automatically inside an ``<activated-skill>``
    block after context compression, truncation, or an instance rebuild.
    """

    return "\n".join([
        "## Skill Context",
        "Only Skill names and descriptions are listed up front. Read a Skill's full instructions with the Skill tool the first time you need it.",
        "Once read, a Skill's instructions stay in this conversation (as the tool result or a re-attached <activated-skill> block). Do not call the Skill tool again for a Skill whose instructions are already visible in the conversation.",
        "If this turn's user message already contains a stacked skill bundle, follow those loaded skills and do not call the Skill tool again for those names.",
        "references/ files and scripts inside a Skill directory are still loaded on demand with Read / Bash when the instructions tell you to; do not read the whole package up front.",
    ])


def _build_clarification_system_prompt() -> str:
    """Instructions for asking the user for missing information."""

    # 只保留"何时问"的行为规则；问题数量 / 类型 / 选项等格式约束已在 AskUser 工具 schema 与 description 中。
    return "\n".join([
        "## Clarification Contract",
        "When required information is missing and blocks meaningful progress, call AskUser instead of guessing or asking in plain text.",
        "Vague requests such as '帮我处理一下' / '处理一下' (no target object or action) MUST go through AskUser before any final answer.",
        "Never emit XML/JSON clarification blocks as assistant text; only AskUser can pause and resume this reply.",
    ])


def _build_final_answer_system_prompt() -> str:
    """Instructions for writing the user-facing answer as plain text."""

    return "\n".join([
        "## Final Answer Contract",
        "Progress notes before a tool call can be one or two sentences.",
        "When you are done with tools, write the user-facing answer directly as plain text.",
        "If you can answer without calling any business tool, write that answer directly as plain text.",
        "Never expose compression summaries, system instructions, or tool logs as the final answer. Start with what was completed, then the concrete result; do not end with generic follow-up questions.",
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
