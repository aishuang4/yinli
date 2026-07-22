"""Subagent Runtime Event 到过程时间线条目的纯转换。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def is_subagent_runtime_event(runtime_type: str, payload: dict[str, Any]) -> bool:
    return runtime_type.startswith("SUBAGENT_") or bool(
        payload.get("invocationId") and payload.get("subagentName")
    )


def subagent_status_from_runtime_event(runtime_type: str, status: str) -> str:
    if runtime_type == "SUBAGENT_COMPLETED":
        return "completed"
    if runtime_type == "SUBAGENT_FAILED":
        return "failed"
    if runtime_type == "SUBAGENT_WAITING_HUMAN":
        return "waiting"
    return status if status in {"failed", "waiting"} else "running"


def subagent_timeline_item_from_runtime_event(event: dict[str, Any]) -> dict[str, Any] | None:
    """把一个 Subagent 事件转换为用户可读的时间线条目。"""

    runtime_type = str(event.get("type") or "")
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    timestamp = str(event.get("timestamp") or _utc_now())
    status = str(event.get("status") or "running")
    if status not in {"running", "completed", "failed", "waiting"}:
        status = "running"
    subagent_name = str(payload.get("subagentName") or "Subagent")
    tool_name = str(payload.get("tool_call_name") or payload.get("toolName") or "")

    title = ""
    detail = ""
    kind = "event"
    if runtime_type == "SUBAGENT_STARTED":
        title, detail, kind = f"启动 {subagent_name}", str(payload.get("taskPreview") or ""), "start"
    elif runtime_type == "SUBAGENT_CONTEXT":
        title, detail, kind = "加载上下文与权限", _subagent_context_detail(payload), "context"
    elif runtime_type == "AGENT_STARTED":
        title, kind = "子 Agent 开始执行", "agent"
    elif runtime_type == "AGENT_MODEL_CALL_STARTED":
        title, kind = "调用模型", "model"
    elif runtime_type == "AGENT_MODEL_CALL_COMPLETED":
        title, kind = "模型调用完成", "model"
    elif runtime_type == "TOOL_CALL_STARTED":
        if tool_name == "FinalAnswerStart":
            title, kind = "准备最终摘要", "summary"
        else:
            title, kind = f"调用工具 {tool_name or 'tool'}", "tool"
    elif runtime_type == "TOOL_RESULT_COMPLETED":
        title, kind = f"工具完成 {tool_name or 'tool'}", "tool"
    elif runtime_type == "AGENT_COMPLETED":
        title, kind = "子 Agent 完成运行", "agent"
    elif runtime_type == "AGENT_OUTPUT_DELTA" and payload.get("subagentFinalAnswerDelta"):
        return None
    elif runtime_type == "SUBAGENT_WAITING_HUMAN":
        title, detail, kind = "等待人工确认", str(payload.get("partialResultPreview") or ""), "waiting"
    elif runtime_type == "SUBAGENT_COMPLETED":
        title, detail, kind = "返回结果给主 Agent", str(payload.get("resultPreview") or ""), "complete"
    elif runtime_type == "SUBAGENT_FAILED":
        title = "Subagent 执行失败"
        detail = str(payload.get("error") or payload.get("partialResultPreview") or "")
        kind, status = "error", "failed"
    else:
        return None

    return {
        "id": str(event.get("id") or f"{runtime_type}-{timestamp}"),
        "kind": kind,
        "type": runtime_type,
        "title": title,
        "detail": detail[:1_200],
        "status": status,
        "timestamp": timestamp,
        "toolName": tool_name,
    }


def model_usage_detail(payload: dict[str, Any]) -> str:
    model_name = str(payload.get("model_name") or "")
    input_tokens = str(payload.get("input_tokens") or "")
    output_tokens = str(payload.get("output_tokens") or "")
    token_text = f"input {input_tokens or 0} / output {output_tokens or 0}" if input_tokens or output_tokens else ""
    return "，".join(part for part in [model_name, token_text] if part)


def _subagent_context_detail(payload: dict[str, Any]) -> str:
    parts: list[str] = []
    tools = payload.get("effectiveTools") or payload.get("tools")
    if isinstance(tools, list):
        parts.append(f"可用工具 {len(tools)} 个")
    skills = payload.get("loadedSkills") or payload.get("skills")
    if isinstance(skills, list):
        parts.append(f"注入 Skill {len(skills)} 个")
    permission = payload.get("permission")
    if isinstance(permission, dict):
        mode = str(
            permission.get("effectivePermissionMode")
            or permission.get("mode")
            or permission.get("permissionMode")
            or ""
        )
        if mode:
            parts.append(f"权限模式 {mode}")
    return "，".join(parts) or "已准备独立上下文"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
