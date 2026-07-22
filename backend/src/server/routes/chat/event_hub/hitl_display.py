"""HITL / clarification 工具调用的展示数据解析。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


def ask_user_tool_call_from_payload(data: dict[str, Any]) -> dict[str, Any] | None:
    """从外部执行 payload 中找到 AskUser 工具调用。"""

    for tool_call in data.get("toolCalls") or data.get("tool_calls") or []:
        if not isinstance(tool_call, dict):
            continue
        if is_ask_user_tool_name(str(tool_call.get("rawName") or tool_call.get("name") or "")):
            return tool_call
    return None


def ask_user_tool_call_from_approval(approval: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(approval, dict):
        return None
    return ask_user_tool_call_from_payload({"toolCalls": approval.get("toolCalls") or []})


def clarification_tool_call_from_payload(data: dict[str, Any]) -> dict[str, Any] | None:
    interaction_id = str(data.get("interactionId") or data.get("id") or "")
    if not interaction_id or str(data.get("kind") or data.get("type") or "") != "clarification":
        return None
    return {
        "id": f"clarification-{interaction_id}",
        "name": "AskUser",
        "input": {
            "title": str(data.get("title") or "需要补充信息"),
            "description": str(data.get("description") or ""),
            "questions": data.get("questions") if isinstance(data.get("questions"), list) else [],
        },
    }


def clarification_tool_call_from_approval(approval: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(approval, dict):
        return {}
    return clarification_tool_call_from_payload(approval) or {}


def format_ask_user_question_preview(tool_call: dict[str, Any]) -> str:
    """把 AskUser 结构化问题格式化为紧凑过程预览。"""

    raw_input = tool_call.get("input") or tool_call.get("args") or {}
    parsed = parse_tool_input(raw_input)
    if not parsed:
        return str(raw_input or "").strip()
    lines: list[str] = []
    title = str(parsed.get("title") or "需要补充信息").strip()
    if title:
        lines.append(f"模型反问：{title}")
    description = str(parsed.get("description") or "").strip()
    if description:
        lines.append(f"说明：{description}")
    questions = parsed.get("questions") if isinstance(parsed.get("questions"), list) else []
    for index, question in enumerate(questions, start=1):
        if not isinstance(question, dict):
            continue
        label = str(question.get("label") or question.get("question") or "").strip()
        if not label:
            continue
        question_type = str(question.get("type") or "text").strip()
        options = [str(option).strip() for option in question.get("options") or [] if str(option).strip()]
        suffix = f"（{question_type}）" if question_type else ""
        line = f"{index}. {label}{suffix}"
        if options:
            line += f"\n   选项：{'、'.join(options)}"
        lines.append(line)
    return "\n".join(lines).strip() or json.dumps(parsed, ensure_ascii=False)


def parse_tool_input(raw_input: Any) -> dict[str, Any]:
    if isinstance(raw_input, dict):
        return raw_input
    if not isinstance(raw_input, str) or not raw_input.strip():
        return {}
    try:
        data = json.loads(raw_input.strip())
        return data if isinstance(data, dict) else {}
    except (TypeError, ValueError):
        return {}


def ask_user_result_preview(tool_id: str, payload: dict[str, Any]) -> str:
    previews = payload.get("externalResultsPreview")
    if isinstance(previews, dict):
        if tool_id in previews:
            return str(previews.get(tool_id) or "")
        if "answer" in previews:
            return str(previews.get("answer") or "")
        values = [str(value or "") for value in previews.values() if str(value or "").strip()]
        if values:
            return "\n".join(values)
    if str(payload.get("action") or "") != "approve":
        return "用户取消或拒绝补充信息。"
    return ""


def is_ask_user_tool_name(name: str) -> bool:
    normalized = "".join(str(name or "").split()).lower()
    return normalized == "askuser" or normalized.endswith("_askuser")


def hitl_process_item_from_required_event(state: dict[str, Any], data: dict[str, Any]) -> dict[str, Any] | None:
    tool_call = ask_user_tool_call_from_payload(data)
    if not tool_call:
        tool_call = clarification_tool_call_from_payload(data)
    if not tool_call:
        return None
    tool_id = str(tool_call.get("id") or "")
    if not tool_id:
        return None
    updated_at = str(state.get("updatedAt") or _utc_now())
    return {
        "id": tool_id,
        "event": "tool.started",
        "targetId": tool_id,
        "status": "waiting",
        "createdAt": updated_at,
        "updatedAt": updated_at,
        "data": {
            "name": "AskUser",
            "inputPreview": format_ask_user_question_preview(tool_call),
            "input": format_ask_user_question_preview(tool_call),
            "outputPreview": "",
            "output": "",
            "outputSize": 0,
            "outputTruncated": False,
            "error": "",
        },
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
