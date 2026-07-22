"""RunState 领域对象的写入、查找与展示数据转换。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _upsert_tool(state: dict[str, Any], data: dict[str, Any], *, status: str) -> dict[str, Any]:
    tool_id = str(data.get("tool_call_id") or data.get("toolCallId") or data.get("id") or "")
    if not tool_id:
        tool_id = f"tool-{len(state.get('tools', [])) + 1}"
    tools = list(state.get("tools") or [])
    index = next((idx for idx, item in enumerate(tools) if item.get("id") == tool_id), -1)
    current = tools[index] if index >= 0 else {}
    tool = {
        "id": tool_id,
        "name": str(data.get("tool_call_name") or data.get("toolName") or current.get("name") or "tool"),
        "status": status,
        "inputPreview": str(current.get("inputPreview") or data.get("input") or ""),
        "outputPreview": str(current.get("outputPreview") or ""),
        "outputSize": int(current.get("outputSize") or 0),
        "outputTruncated": bool(current.get("outputTruncated") or False),
        "error": str(current.get("error") or ""),
        "startedAt": current.get("startedAt") or _utc_now(),
        "completedAt": current.get("completedAt") or "",
    }
    for key in [
        "subagentName",
        "subagentInvocationId",
        "subagentStatus",
        "subagentTimeline",
        "subagentSummary",
        "subagentError",
    ]:
        if key in current:
            tool[key] = current[key]
    if index >= 0:
        tools[index] = tool
    else:
        tools.append(tool)
    state["tools"] = tools[-200:]
    return tool


def _upsert_approval(state: dict[str, Any], data: dict[str, Any], approval_type: str = "hitl") -> None:
    interaction_id = str(data.get("interactionId") or data.get("id") or "")
    if not interaction_id:
        return
    approvals = list(state.get("approvals") or [])
    interaction_type = (
        "clarification"
        if approval_type == "clarification"
        else str(data.get("type") or approval_type)
    )
    next_item = {
        "interactionId": interaction_id,
        "kind": approval_type,
        "type": interaction_type,
        "status": "waiting",
        "agentId": str(data.get("agentId") or data.get("agent_id") or ""),
        "agentName": str(data.get("agentName") or data.get("agent_name") or ""),
        "scope": str(data.get("scope") or ""),
        "toolCalls": data.get("toolCalls") or data.get("tool_calls") or [],
        "title": str(data.get("title") or ""),
        "description": str(data.get("description") or ""),
        "questions": data.get("questions") if isinstance(data.get("questions"), list) else [],
        "createdAt": str(data.get("createdAt") or data.get("created_at") or _utc_now()),
    }
    index = next((idx for idx, item in enumerate(approvals) if item.get("interactionId") == interaction_id), -1)
    if index >= 0:
        approvals[index] = {**approvals[index], **next_item}
    else:
        approvals.append(next_item)
    state["approvals"] = approvals[-50:]


def _upsert_plan(state: dict[str, Any], event: dict[str, Any]) -> None:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    plan_id = str(payload.get("planId") or "")
    if not plan_id:
        return
    plans = list(state.get("plans") or [])
    index = next((idx for idx, item in enumerate(plans) if item.get("planId") == plan_id), -1)
    current = plans[index] if index >= 0 else {}
    next_plan = {
        **current,
        "planId": plan_id,
        "planFile": str(payload.get("planFile") or current.get("planFile") or ""),
        "status": str(event.get("status") or current.get("status") or "running"),
        "markdown": str(payload.get("markdown") or current.get("markdown") or ""),
        "steps": payload.get("steps") or current.get("steps") or [],
        "updatedAt": str(event.get("timestamp") or _utc_now()),
    }
    if index >= 0:
        plans[index] = next_plan
    else:
        plans.append(next_plan)
    state["plans"] = plans[-20:]


def _replace_skill_calls(state: dict[str, Any], data: dict[str, Any], *, status: str) -> None:
    skills = data.get("skills")
    if not isinstance(skills, list):
        return
    existing = {
        str(skill.get("id") or skill.get("name") or ""): skill
        for skill in state.get("skillCalls", [])
        if isinstance(skill, dict)
    }
    next_calls = []
    for index, skill in enumerate(skills):
        skill_data = skill if isinstance(skill, dict) else {}
        skill_id = str(skill_data.get("id") or skill_data.get("name") or index)
        current = existing.get(skill_id, {})
        next_calls.append({
            "id": skill_id,
            "name": str(skill_data.get("name") or current.get("name") or skill_data.get("id") or "Skill"),
            "description": str(skill_data.get("description") or current.get("description") or ""),
            "trigger": str(skill_data.get("trigger") or current.get("trigger") or ""),
            "content": str(skill_data.get("content") or current.get("content") or ""),
            "resourcePath": str(
                skill_data.get("resourcePath")
                or skill_data.get("resource_path")
                or current.get("resourcePath")
                or ""
            ),
            "resources": [
                str(item)
                for item in (skill_data.get("resources") or current.get("resources") or [])
            ],
            "status": status,
        })
    state["skillCalls"] = next_calls[-50:]


def _append_model_cache(state: dict[str, Any], data: dict[str, Any]) -> None:
    input_tokens = int(data.get("input_tokens") or data.get("inputTokens") or 0)
    output_tokens = int(data.get("output_tokens") or data.get("outputTokens") or 0)
    model_name = str(data.get("model_name") or data.get("modelName") or state.get("modelName") or "")
    if model_name:
        state["modelName"] = model_name
    state["tokenUsage"] = {
        "input": input_tokens,
        "output": output_tokens,
    }


def _has_runtime_event_type(state: dict[str, Any], event_types: set[str]) -> bool:
    return any(
        isinstance(event, dict) and str(event.get("type") or "") in event_types
        for event in state.get("runtimeEvents") or []
    )


def _final_answer_display_status(run_status: str) -> str:
    if run_status in {"completed", "failed", "cancelled"}:
        return run_status
    return "running"


def _find_tool(state: dict[str, Any], tool_id: str) -> dict[str, Any] | None:
    for tool in state.get("tools") or []:
        if isinstance(tool, dict) and str(tool.get("id") or "") == tool_id:
            return tool
    return None


def _find_approval(state: dict[str, Any], interaction_id: str) -> dict[str, Any] | None:
    for approval in state.get("approvals") or []:
        if isinstance(approval, dict) and str(approval.get("interactionId") or "") == interaction_id:
            return approval
    return None


def _find_plan(state: dict[str, Any], plan_id: str) -> dict[str, Any] | None:
    for plan in state.get("plans") or []:
        if isinstance(plan, dict) and str(plan.get("planId") or "") == plan_id:
            return plan
    return None


def _tool_display_data(tool: dict[str, Any]) -> dict[str, Any]:
    input_text = str(tool.get("input") or tool.get("inputPreview") or "")
    output_text = str(tool.get("output") or tool.get("outputPreview") or "")
    data = {
        "name": str(tool.get("name") or "tool"),
        "inputPreview": input_text,
        "input": input_text,
        "outputPreview": output_text,
        "output": output_text,
        "outputSize": int(tool.get("outputSize") or 0),
        "outputTruncated": bool(tool.get("outputTruncated") or False),
        "error": str(tool.get("error") or ""),
        "startedAt": str(tool.get("startedAt") or ""),
        "completedAt": str(tool.get("completedAt") or ""),
    }
    for key in [
        "subagentName",
        "subagentInvocationId",
        "subagentStatus",
        "subagentTimeline",
        "subagentSummary",
        "subagentError",
    ]:
        if key in tool:
            data[key] = tool[key]
    return data


def _tool_display_event_name(tool: dict[str, Any]) -> str:
    status = str(tool.get("status") or "running")
    if status == "completed":
        return "tool.completed"
    if status == "failed":
        return "tool.failed"
    return "tool.started"


def _skill_display_event_name(skill: dict[str, Any]) -> str:
    status = str(skill.get("status") or "running")
    if status == "completed":
        return "skill.completed"
    if status == "failed":
        return "skill.failed"
    return "skill.started"


def _finalize_open_tools(state: dict[str, Any]) -> None:
    """Run 已终止时关闭仍未返回结果的工具摘要，防止前端无限显示运行中。"""

    finalized = []
    for tool in state.get("tools") or []:
        if not isinstance(tool, dict):
            finalized.append(tool)
            continue
        if tool.get("status") in {"calling", "running"}:
            finalized.append({
                **tool,
                "status": "failed",
                "error": str(tool.get("error") or "运行已结束，但该工具没有返回结果。"),
                "completedAt": str(tool.get("completedAt") or _utc_now()),
            })
        else:
            finalized.append(tool)
    state["tools"] = finalized


def _tool_name(data: dict[str, Any]) -> str:
    return str(data.get("tool_call_name") or data.get("toolName") or "工具")


def _append_text(state: dict[str, Any], key: str, delta: str, limit: int) -> None:
    if not delta:
        return
    state[key] = _truncate(str(state.get(key) or "") + delta, limit)


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[:limit]

def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
