"""RunState 投影器。

把底层 AgentScope/RUNTIME_EVENT 原始事件转换成 Chat 前端可直接恢复的轻量状态。
工具结果只保留摘要，完整 Observation 不进入 RunState。
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from src.server.routes.chat.event_types import SSE_EVENT_RUNTIME
from src.server.routes.chat.run_state_schema import dump_run_state


TOOL_INPUT_PREVIEW_MAX_CHARS = 2_000
TOOL_OUTPUT_PREVIEW_MAX_CHARS = 4_000
REASONING_MAX_CHARS = 100_000
FINAL_ANSWER_MAX_CHARS = 300_000


def initial_run_state(run: Any) -> dict[str, Any]:
    """创建一个新的 RunState 快照。"""

    now = _utc_now()
    return {
        "version": 1,
        "runId": run.id,
        "sessionId": run.session_id,
        "agentId": run.agent_id,
        "agentName": run.agent_name,
        "teamId": run.team_id,
        "teamName": run.team_name,
        "mode": run.mode,
        "status": run.status,
        "task": run.task_text,
        "createdAt": run.created_at,
        "updatedAt": now,
        "completedAt": run.completed_at,
        "currentStage": "准备执行",
        "reasoning": "",
        "reasoningTruncated": False,
        "finalAnswer": "",
        "finalAnswerTruncated": False,
        "thinking": "",
        "tools": [],
        "teamActivities": [],
        "skillCalls": [],
        "modelCaches": [],
        "modelName": "",
        "tokenUsage": None,
        "plans": [],
        "approvals": [],
        "runtimeEvents": [],
        "error": "",
        "lastEventType": "",
        "eventCount": 0,
    }


def normalize_run_state(state: dict[str, Any]) -> dict[str, Any]:
    """归一化 RunState 快照，避免终态 Run 残留运行中工具。"""

    next_state = deepcopy(state)
    if str(next_state.get("status") or "") in {"completed", "failed", "cancelled"}:
        _finalize_open_tools(next_state)
    return dump_run_state(next_state)


def project_event(state: dict[str, Any] | None, event_type: str, data: dict[str, Any], run: Any) -> dict[str, Any]:
    """根据一条 SSE 事件更新 RunState。"""

    next_state = deepcopy(state) if state else initial_run_state(run)
    next_state["status"] = run.status
    next_state["completedAt"] = run.completed_at
    next_state["updatedAt"] = _utc_now()
    next_state["lastEventType"] = event_type
    next_state["eventCount"] = int(next_state.get("eventCount") or 0) + 1

    if event_type == "RUN":
        next_state["currentStage"] = "准备执行"
    elif event_type == "REASONING_DELTA":
        _append_text(next_state, "reasoning", str(data.get("delta") or ""), REASONING_MAX_CHARS)
        next_state["reasoningTruncated"] = len(next_state["reasoning"]) >= REASONING_MAX_CHARS
        next_state["currentStage"] = "正在分析"
    elif event_type == "FINAL_ANSWER_DELTA":
        _append_text(next_state, "finalAnswer", str(data.get("delta") or ""), FINAL_ANSWER_MAX_CHARS)
        next_state["finalAnswerTruncated"] = len(next_state["finalAnswer"]) >= FINAL_ANSWER_MAX_CHARS
        next_state["currentStage"] = "正在生成最终答案"
    elif event_type == "TEXT_BLOCK_DELTA":
        _append_text(next_state, "reasoning", str(data.get("delta") or ""), REASONING_MAX_CHARS)
        next_state["reasoningTruncated"] = len(next_state["reasoning"]) >= REASONING_MAX_CHARS
    elif event_type == "THINKING_BLOCK_DELTA":
        _append_text(next_state, "thinking", str(data.get("delta") or ""), REASONING_MAX_CHARS)
    elif event_type == "TOOL_CALL_START":
        _upsert_tool(next_state, data, status="running")
        next_state["currentStage"] = f"正在调用 {_tool_name(data)}"
    elif event_type == "TOOL_CALL_DELTA":
        tool = _upsert_tool(next_state, data, status="running")
        tool["inputPreview"] = _truncate(
            str(tool.get("inputPreview") or "") + str(data.get("delta") or ""),
            TOOL_INPUT_PREVIEW_MAX_CHARS,
        )
    elif event_type == "TOOL_CALL_END":
        _upsert_tool(next_state, data, status="running")
    elif event_type == "TOOL_RESULT_TEXT_DELTA":
        tool = _upsert_tool(next_state, data, status="running")
        delta = str(data.get("delta") or "")
        tool["outputSize"] = int(tool.get("outputSize") or 0) + len(delta)
        tool["outputPreview"] = _truncate(
            str(tool.get("outputPreview") or "") + delta,
            TOOL_OUTPUT_PREVIEW_MAX_CHARS,
        )
        tool["outputTruncated"] = tool["outputSize"] > TOOL_OUTPUT_PREVIEW_MAX_CHARS
    elif event_type == "TOOL_RESULT_END":
        status = "failed" if str(data.get("state") or "").lower() in {"error", "failed", "denied"} else "completed"
        tool = _upsert_tool(next_state, data, status=status)
        tool["completedAt"] = _utc_now()
        if status == "failed":
            tool["error"] = str(data.get("error") or data.get("message") or data.get("state") or "工具调用失败")
    elif event_type == "TEAM_ACTIVITY":
        activities = data.get("activities")
        next_state["teamActivities"] = activities if isinstance(activities, list) else []
    elif event_type == "SKILL_CONTEXT":
        _replace_skill_calls(next_state, data, status="running")
    elif event_type == "SKILL_END":
        status = "failed" if data.get("status") == "failed" else "completed"
        _replace_skill_calls(next_state, data, status=status)
    elif event_type == "MODEL_CALL_START":
        next_state["modelName"] = str(data.get("model_name") or data.get("modelName") or next_state.get("modelName") or "")
    elif event_type == "MODEL_CALL_END":
        _append_model_cache(next_state, data)
    elif event_type == "HITL_REQUIRED":
        _upsert_approval(next_state, data)
        next_state["status"] = "waiting"
        next_state["currentStage"] = "等待人工处理"
    elif event_type == "CLARIFICATION_REQUIRED":
        _upsert_approval(next_state, data, approval_type="clarification")
        next_state["status"] = "waiting"
        next_state["currentStage"] = "等待用户补充信息"
    elif event_type == "ERROR":
        next_state["status"] = "failed"
        next_state["error"] = str(data.get("message") or "执行失败")
        next_state["currentStage"] = "执行失败"
    elif event_type == SSE_EVENT_RUNTIME:
        _project_runtime_event(next_state, data)

    if next_state["status"] == "completed":
        next_state["currentStage"] = "已完成"
    elif next_state["status"] == "failed":
        next_state["currentStage"] = "失败"
    elif next_state["status"] == "cancelled":
        next_state["currentStage"] = "已取消"
    if next_state["status"] in {"completed", "failed", "cancelled"}:
        _finalize_open_tools(next_state)

    return next_state


def _project_runtime_event(state: dict[str, Any], event: dict[str, Any]) -> None:
    runtime_type = str(event.get("type") or "")
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    state["runtimeEvents"] = [*state.get("runtimeEvents", [])[-199:], event]
    if runtime_type == "HITL_RESOLVED":
        interaction_id = str(payload.get("interactionId") or event.get("interactionId") or "")
        if interaction_id:
            state["approvals"] = [
                {**item, "status": "resolved"} if item.get("interactionId") == interaction_id else item
                for item in state.get("approvals", [])
            ]
    elif runtime_type.startswith("PLAN_"):
        _upsert_plan(state, event)
    elif runtime_type in {"RUN_COMPLETED", "RUN_FAILED", "RUN_CANCELLED"}:
        state["status"] = {
            "RUN_COMPLETED": "completed",
            "RUN_FAILED": "failed",
            "RUN_CANCELLED": "cancelled",
        }[runtime_type]


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
    cache_creation_input_tokens = int(data.get("cache_creation_input_tokens") or 0)
    cache_input_tokens = int(data.get("cache_input_tokens") or 0)
    model_name = str(data.get("model_name") or data.get("modelName") or state.get("modelName") or "")
    if model_name:
        state["modelName"] = model_name
    state["tokenUsage"] = {
        "input": input_tokens,
        "output": output_tokens,
    }
    if cache_creation_input_tokens <= 0 and cache_input_tokens <= 0:
        return
    cache_base_tokens = input_tokens + cache_input_tokens
    state["modelCaches"] = [
        *list(state.get("modelCaches") or [])[-49:],
        {
            "id": f"provider-cache-{_utc_now()}",
            "modelName": model_name,
            "inputTokens": input_tokens,
            "outputTokens": output_tokens,
            "cacheCreationInputTokens": cache_creation_input_tokens,
            "cacheInputTokens": cache_input_tokens,
            "cacheHitRate": cache_input_tokens / cache_base_tokens if cache_base_tokens > 0 else 0,
            "estimatedSavedInputTokens": cache_input_tokens,
            "createdAt": _utc_now(),
        },
    ]


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
