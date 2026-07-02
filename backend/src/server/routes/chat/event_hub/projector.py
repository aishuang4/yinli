"""RunState 投影器。

把底层 AgentScope/RUNTIME_EVENT 原始事件转换成 Chat 前端可直接恢复的轻量状态。
工具结果只保留摘要，完整 Observation 不进入 RunState。
"""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any

from src.server.routes.chat.event_types import SSE_EVENT_RUNTIME
from src.server.routes.chat.run_state_schema import dump_run_state


TOOL_INPUT_PREVIEW_MAX_CHARS = 2_000
TOOL_OUTPUT_PREVIEW_MAX_CHARS = 4_000
REASONING_MAX_CHARS = 100_000
FINAL_ANSWER_MAX_CHARS = 300_000
DISPLAY_SNAPSHOT_VERSION = 2
DISPLAY_EVENT_SCOPE = "single_agent"
PROCESS_TIMELINE_KEY = "_processTimelineItems"


def initial_run_state(run: Any) -> dict[str, Any]:
    """创建一个新的 RunState 快照。"""

    now = _utc_now()
    return {
        "version": 2,
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
        "modelName": "",
        "tokenUsage": None,
        "plans": [],
        "displaySnapshot": _empty_display_snapshot(),
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
    refresh_run_state_display_snapshot(next_state)
    return dump_run_state(next_state)


def refresh_run_state_display_snapshot(state: dict[str, Any]) -> dict[str, Any]:
    """刷新单 Agent 展示快照。

    展示快照只从当前 RunState 字段派生，避免前端主界面扫描 RuntimeEvent。
    """

    state["displaySnapshot"] = _build_display_snapshot(state)
    return state


def consume_run_state_display_events(state: dict[str, Any]) -> list[dict[str, Any]]:
    """取出本次投影产生的展示增量事件，并从 RunState 中移除临时字段。"""

    events = state.pop("_displayEvents", [])
    state.pop("_lastDisplayEvent", None)
    return [event for event in events if isinstance(event, dict)]


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
    _project_process_timeline(next_state, event_type, data)
    refresh_run_state_display_snapshot(next_state)
    next_state["_displayEvents"] = _build_display_events_for_raw_event(next_state, event_type, data)

    return next_state


def _empty_display_snapshot() -> dict[str, Any]:
    return {
        "version": DISPLAY_SNAPSHOT_VERSION,
        "blocks": [],
    }


def _build_display_snapshot(state: dict[str, Any]) -> dict[str, Any]:
    status = str(state.get("status") or "running")
    updated_at = str(state.get("updatedAt") or _utc_now())
    blocks: list[dict[str, Any]] = []
    reasoning = str(state.get("reasoning") or "")
    thinking = str(state.get("thinking") or "")
    tools = [tool for tool in state.get("tools") or [] if isinstance(tool, dict)]
    skill_calls = [skill for skill in state.get("skillCalls") or [] if isinstance(skill, dict)]
    process_items = _build_process_items_from_timeline(state, status, updated_at) or _build_process_items(
        reasoning,
        thinking,
        tools,
        skill_calls,
        status,
        updated_at,
    )
    if process_items:
        blocks.append({
            "id": "process",
            "block": "process",
            "status": status,
            "updatedAt": updated_at,
            "items": process_items,
            "data": {},
        })

    waiting_feedback_items = [
        approval
        for approval in state.get("approvals") or []
        if isinstance(approval, dict) and approval.get("status") == "waiting"
    ]
    if waiting_feedback_items:
        blocks.append({
            "id": "feedback",
            "block": "feedback",
            "status": "waiting",
            "updatedAt": updated_at,
            "items": [],
            "data": {"approval": waiting_feedback_items[-1]},
        })

    for plan in state.get("plans") or []:
        if not isinstance(plan, dict):
            continue
        plan_id = str(plan.get("planId") or "")
        if not plan_id:
            continue
        blocks.append({
            "id": f"plan-{plan_id}",
            "block": "plan",
            "status": str(plan.get("status") or "running"),
            "updatedAt": str(plan.get("updatedAt") or updated_at),
            "items": [],
            "data": {
                "planId": plan_id,
                "planFile": str(plan.get("planFile") or ""),
                "markdown": str(plan.get("markdown") or ""),
                "steps": plan.get("steps") if isinstance(plan.get("steps"), list) else [],
            },
        })

    final_answer = str(state.get("finalAnswer") or "")
    final_answer_started = bool(final_answer or _has_runtime_event_type(
        state,
        {"AGENT_FINAL_ANSWER_STARTED", "AGENT_FINAL_ANSWER_DELTA"},
    ))
    if final_answer_started:
        blocks.append({
            "id": "answer",
            "block": "answer",
            "status": _final_answer_display_status(status),
            "updatedAt": updated_at,
            "items": [],
            "data": {
                "content": final_answer,
                "started": True,
            },
        })

    generated_files = _collect_generated_files_from_tools(tools)
    if generated_files:
        blocks.append({
            "id": "files",
            "block": "files",
            "status": "completed",
            "updatedAt": updated_at,
            "items": [],
            "data": {"files": generated_files},
        })

    error = str(state.get("error") or "")
    if error:
        blocks.append({
            "id": "error",
            "block": "error",
            "status": "failed",
            "updatedAt": updated_at,
            "items": [],
            "data": {"message": error},
        })

    return {
        "version": DISPLAY_SNAPSHOT_VERSION,
        "blocks": blocks,
    }


def _build_process_items_from_timeline(
    state: dict[str, Any],
    status: str,
    updated_at: str,
) -> list[dict[str, Any]]:
    timeline = state.get(PROCESS_TIMELINE_KEY)
    if not isinstance(timeline, list):
        return []
    items: list[dict[str, Any]] = []
    for item in timeline:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or item.get("targetId") or "")
        event_name = str(item.get("event") or "")
        if not item_id or not event_name:
            continue
        current_tool = _find_tool(state, item_id) if event_name.startswith("tool.") else None
        if current_tool:
            event_name = _tool_display_event_name(current_tool)
            item = {
                **item,
                "event": event_name,
                "status": str(current_tool.get("status") or item.get("status") or status),
                "updatedAt": str(current_tool.get("completedAt") or item.get("updatedAt") or updated_at),
                "data": _tool_display_data(current_tool),
            }
        item_status = str(item.get("status") or status)
        items.append({
            "id": item_id,
            "event": event_name,
            "targetId": str(item.get("targetId") or item_id),
            "status": item_status,
            "createdAt": str(item.get("createdAt") or updated_at),
            "updatedAt": str(item.get("updatedAt") or updated_at),
            "data": item.get("data") if isinstance(item.get("data"), dict) else {},
        })
    return items


def _build_process_items(
    reasoning: str,
    thinking: str,
    tools: list[dict[str, Any]],
    skill_calls: list[dict[str, Any]],
    status: str,
    updated_at: str,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if reasoning:
        items.append({
            "id": "reasoning",
            "event": "reasoning.delta",
            "targetId": "reasoning",
            "status": status,
            "createdAt": updated_at,
            "updatedAt": updated_at,
            "data": {"text": reasoning},
        })
    if thinking:
        items.append({
            "id": "thinking",
            "event": "thinking.delta",
            "targetId": "thinking",
            "status": status,
            "createdAt": updated_at,
            "updatedAt": updated_at,
            "data": {"text": thinking},
        })
    for tool in tools:
        items.append({
            "id": str(tool.get("id") or ""),
            "event": _tool_display_event_name(tool),
            "targetId": str(tool.get("id") or ""),
            "status": str(tool.get("status") or "running"),
            "createdAt": str(tool.get("startedAt") or updated_at),
            "updatedAt": str(tool.get("completedAt") or updated_at),
            "data": _tool_display_data(tool),
        })
    for skill in skill_calls:
        skill_id = str(skill.get("id") or skill.get("name") or "")
        items.append({
            "id": skill_id,
            "event": _skill_display_event_name(skill),
            "targetId": skill_id,
            "status": str(skill.get("status") or "running"),
            "createdAt": updated_at,
            "updatedAt": updated_at,
            "data": {
                "name": str(skill.get("name") or "Skill"),
                "description": str(skill.get("description") or ""),
                "trigger": str(skill.get("trigger") or ""),
                "content": str(skill.get("content") or ""),
                "resourcePath": str(skill.get("resourcePath") or ""),
                "resources": skill.get("resources") if isinstance(skill.get("resources"), list) else [],
            },
        })
    return items


def _project_process_timeline(state: dict[str, Any], event_type: str, data: dict[str, Any]) -> None:
    """维护 process block 的流式顺序。

    reasoning / thinking 是流式文本，只有相邻片段才合并；中间出现工具或
    skill 后，新文本必须成为新的 item，前端才能按 ReAct 顺序展示。
    """

    if event_type in {"REASONING_DELTA", "TEXT_BLOCK_DELTA"}:
        _append_process_text_timeline_item(
            state,
            event_name="reasoning.delta",
            target_prefix="reasoning",
            text=str(data.get("delta") or ""),
        )
        return
    if event_type == "THINKING_BLOCK_DELTA":
        _append_process_text_timeline_item(
            state,
            event_name="thinking.delta",
            target_prefix="thinking",
            text=str(data.get("delta") or ""),
        )
        return
    if event_type in {"TOOL_CALL_START", "TOOL_CALL_DELTA", "TOOL_CALL_END", "TOOL_RESULT_TEXT_DELTA", "TOOL_RESULT_END"}:
        _upsert_process_tool_timeline_item(state, data)
        return
    if event_type in {"SKILL_CONTEXT", "SKILL_END"}:
        _upsert_process_skill_timeline_items(state)


def _process_timeline_items(state: dict[str, Any]) -> list[dict[str, Any]]:
    items = state.get(PROCESS_TIMELINE_KEY)
    if not isinstance(items, list):
        items = []
    normalized = [item for item in items if isinstance(item, dict)]
    state[PROCESS_TIMELINE_KEY] = normalized
    return normalized


def _append_process_text_timeline_item(
    state: dict[str, Any],
    *,
    event_name: str,
    target_prefix: str,
    text: str,
) -> None:
    if not text:
        return
    items = _process_timeline_items(state)
    updated_at = str(state.get("updatedAt") or _utc_now())
    last_item = items[-1] if items else None
    if (
        isinstance(last_item, dict)
        and str(last_item.get("event") or "") == event_name
        and str(last_item.get("targetId") or "").startswith(f"{target_prefix}-")
    ):
        data = last_item.get("data") if isinstance(last_item.get("data"), dict) else {}
        current_text = str(data.get("text") or "")
        last_item["data"] = {**data, "text": current_text + text}
        last_item["status"] = "running"
        last_item["updatedAt"] = updated_at
        return

    target_id = f"{target_prefix}-{int(state.get('eventCount') or 0)}"
    items.append({
        "id": target_id,
        "event": event_name,
        "targetId": target_id,
        "status": "running",
        "createdAt": updated_at,
        "updatedAt": updated_at,
        "data": {"text": text},
    })


def _upsert_process_tool_timeline_item(state: dict[str, Any], data: dict[str, Any]) -> None:
    tool_id = str(data.get("tool_call_id") or data.get("toolCallId") or data.get("id") or "")
    if not tool_id:
        return
    tool = _find_tool(state, tool_id)
    if not tool:
        return
    item = {
        "id": str(tool.get("id") or tool_id),
        "event": _tool_display_event_name(tool),
        "targetId": str(tool.get("id") or tool_id),
        "status": str(tool.get("status") or "running"),
        "createdAt": str(tool.get("startedAt") or state.get("updatedAt") or _utc_now()),
        "updatedAt": str(tool.get("completedAt") or state.get("updatedAt") or _utc_now()),
        "data": _tool_display_data(tool),
    }
    _upsert_process_timeline_item(state, item)


def _upsert_process_skill_timeline_items(state: dict[str, Any]) -> None:
    for skill in state.get("skillCalls") or []:
        if not isinstance(skill, dict):
            continue
        skill_id = str(skill.get("id") or skill.get("name") or "")
        if not skill_id:
            continue
        _upsert_process_timeline_item(state, {
            "id": skill_id,
            "event": _skill_display_event_name(skill),
            "targetId": skill_id,
            "status": str(skill.get("status") or "running"),
            "createdAt": str(state.get("updatedAt") or _utc_now()),
            "updatedAt": str(state.get("updatedAt") or _utc_now()),
            "data": {
                "name": str(skill.get("name") or "Skill"),
                "description": str(skill.get("description") or ""),
                "trigger": str(skill.get("trigger") or ""),
                "content": str(skill.get("content") or ""),
                "resourcePath": str(skill.get("resourcePath") or ""),
                "resources": skill.get("resources") if isinstance(skill.get("resources"), list) else [],
            },
        })


def _upsert_process_timeline_item(state: dict[str, Any], item: dict[str, Any]) -> None:
    items = _process_timeline_items(state)
    target_id = str(item.get("targetId") or item.get("id") or "")
    existing_index = next((
        index
        for index, current in enumerate(items)
        if str(current.get("targetId") or current.get("id") or "") == target_id
    ), -1)
    if existing_index == -1:
        items.append(item)
    else:
        items[existing_index] = {**items[existing_index], **item}


def _latest_process_text_target_id(state: dict[str, Any], event_name: str, fallback: str) -> str:
    for item in reversed(_process_timeline_items(state)):
        if str(item.get("event") or "") == event_name:
            return str(item.get("targetId") or item.get("id") or fallback)
    return fallback


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


def _build_display_events_for_raw_event(
    state: dict[str, Any],
    event_type: str,
    data: dict[str, Any],
) -> list[dict[str, Any]]:
    if event_type in {"REASONING_DELTA", "TEXT_BLOCK_DELTA"}:
        target_id = _latest_process_text_target_id(state, "reasoning.delta", "reasoning")
        return [_display_event(
            state,
            block="process",
            event="reasoning.delta",
            op="append",
            target_id=target_id,
            status="running",
            data={"text": str(data.get("delta") or "")},
        )]
    if event_type == "THINKING_BLOCK_DELTA":
        target_id = _latest_process_text_target_id(state, "thinking.delta", "thinking")
        return [_display_event(
            state,
            block="process",
            event="thinking.delta",
            op="append",
            target_id=target_id,
            status="running",
            data={"text": str(data.get("delta") or "")},
        )]
    if event_type == "FINAL_ANSWER_DELTA":
        return [_display_event(
            state,
            block="answer",
            event="answer.delta",
            op="append",
            target_id="final-answer",
            status=_final_answer_display_status(str(state.get("status") or "running")),
            data={"content": str(data.get("delta") or ""), "started": True},
        )]
    if event_type in {"TOOL_CALL_START", "TOOL_CALL_DELTA", "TOOL_CALL_END", "TOOL_RESULT_TEXT_DELTA", "TOOL_RESULT_END"}:
        return _tool_display_events_for_raw_event(state, event_type, data)
    if event_type in {"SKILL_CONTEXT", "SKILL_END"}:
        return _skill_display_events_for_raw_event(state, event_type)
    if event_type in {"HITL_REQUIRED", "CLARIFICATION_REQUIRED"}:
        interaction_id = str(data.get("interactionId") or data.get("id") or "")
        approval = _find_approval(state, interaction_id)
        return [_display_event(
            state,
            block="feedback",
            event="feedback.waiting",
            op="replace",
            target_id=interaction_id or "feedback",
            status="waiting",
            data={"approval": approval or {}},
        )]
    if event_type == "ERROR":
        return [_display_event(
            state,
            block="error",
            event="error.raised",
            op="replace",
            target_id="error",
            status="failed",
            data={"message": str(data.get("message") or state.get("error") or "执行失败")},
        )]
    if event_type == SSE_EVENT_RUNTIME:
        return _runtime_display_events_for_raw_event(state, data)
    return []


def _tool_display_events_for_raw_event(
    state: dict[str, Any],
    event_type: str,
    data: dict[str, Any],
) -> list[dict[str, Any]]:
    tool_id = str(data.get("tool_call_id") or data.get("toolCallId") or data.get("id") or "")
    tool = _find_tool(state, tool_id)
    if not tool:
        return []
    if event_type == "TOOL_CALL_START":
        event_name = "tool.started"
        op = "upsert"
        event_data = _tool_display_data(tool)
    elif event_type == "TOOL_CALL_DELTA":
        event_name = "tool.input.delta"
        op = "append"
        event_data = {"name": str(tool.get("name") or "tool"), "inputPreview": str(data.get("delta") or "")}
    elif event_type == "TOOL_RESULT_TEXT_DELTA":
        event_name = "tool.output.delta"
        op = "append"
        event_data = {"name": str(tool.get("name") or "tool"), "outputPreview": str(data.get("delta") or "")}
    elif str(tool.get("status") or "") == "failed":
        event_name = "tool.failed"
        op = "upsert"
        event_data = _tool_display_data(tool)
    else:
        event_name = "tool.completed" if event_type == "TOOL_RESULT_END" else "tool.started"
        op = "upsert"
        event_data = _tool_display_data(tool)

    events = [_display_event(
        state,
        block="process",
        event=event_name,
        op=op,
        target_id=str(tool.get("id") or tool_id),
        status=str(tool.get("status") or "running"),
        data=event_data,
    )]
    if event_type == "TOOL_RESULT_END":
        generated_files = _collect_generated_files_from_tools([tool])
        if generated_files:
            events.append(_display_event(
                state,
                block="files",
                event="file.created",
                op="upsert",
                target_id=generated_files[0]["path"],
                status="completed",
                data={"files": _collect_generated_files_from_tools([
                    item for item in state.get("tools") or [] if isinstance(item, dict)
                ])},
            ))
    return events


def _skill_display_events_for_raw_event(state: dict[str, Any], event_type: str) -> list[dict[str, Any]]:
    skill_calls = [skill for skill in state.get("skillCalls") or [] if isinstance(skill, dict)]
    events: list[dict[str, Any]] = []
    for skill in skill_calls:
        skill_id = str(skill.get("id") or skill.get("name") or "")
        status = str(skill.get("status") or "running")
        if event_type == "SKILL_CONTEXT":
            event_name = "skill.started"
        elif status == "failed":
            event_name = "skill.failed"
        else:
            event_name = "skill.completed"
        events.append(_display_event(
            state,
            block="process",
            event=event_name,
            op="upsert",
            target_id=skill_id,
            status=status,
            data={
                "name": str(skill.get("name") or "Skill"),
                "description": str(skill.get("description") or ""),
                "trigger": str(skill.get("trigger") or ""),
                "content": str(skill.get("content") or ""),
                "resourcePath": str(skill.get("resourcePath") or ""),
                "resources": skill.get("resources") if isinstance(skill.get("resources"), list) else [],
            },
        ))
    return events


def _runtime_display_events_for_raw_event(state: dict[str, Any], event: dict[str, Any]) -> list[dict[str, Any]]:
    runtime_type = str(event.get("type") or "")
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    if runtime_type == "HITL_RESOLVED":
        interaction_id = str(payload.get("interactionId") or event.get("interactionId") or "")
        return [_display_event(
            state,
            block="feedback",
            event="feedback.resolved",
            op="remove",
            target_id=interaction_id or "feedback",
            status="completed",
            data={},
        )]
    if runtime_type.startswith("PLAN_"):
        plan_id = str(payload.get("planId") or "")
        plan = _find_plan(state, plan_id)
        if not plan:
            return []
        status = str(plan.get("status") or event.get("status") or "running")
        if runtime_type == "PLAN_FINALIZED":
            event_name = "plan.completed"
        elif status == "failed":
            event_name = "plan.failed"
        elif status == "completed":
            event_name = "plan.completed"
        else:
            event_name = "plan.updated"
        return [_display_event(
            state,
            block="plan",
            event=event_name,
            op="replace",
            target_id=plan_id,
            status=status,
            data={
                "planId": plan_id,
                "planFile": str(plan.get("planFile") or ""),
                "markdown": str(plan.get("markdown") or ""),
                "steps": plan.get("steps") if isinstance(plan.get("steps"), list) else [],
            },
        )]
    if runtime_type in {"RUN_FAILED", "RUN_CANCELLED"} and state.get("error"):
        return [_display_event(
            state,
            block="error",
            event="error.raised",
            op="replace",
            target_id="error",
            status="failed",
            data={"message": str(state.get("error") or "执行失败")},
        )]
    if runtime_type == "RUN_COMPLETED" and state.get("finalAnswer"):
        return [_display_event(
            state,
            block="answer",
            event="answer.completed",
            op="upsert",
            target_id="final-answer",
            status="completed",
            data={"content": str(state.get("finalAnswer") or ""), "started": True},
        )]
    return []


def _display_event(
    state: dict[str, Any],
    *,
    block: str,
    event: str,
    op: str,
    target_id: str,
    status: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    seq = int(state.get("eventCount") or 0)
    created_at = str(state.get("updatedAt") or _utc_now())
    return {
        "id": f"{state.get('runId') or 'run'}-{seq}-{block}-{event}-{target_id}",
        "runId": str(state.get("runId") or ""),
        "sessionId": str(state.get("sessionId") or ""),
        "seq": seq,
        "createdAt": created_at,
        "scope": DISPLAY_EVENT_SCOPE,
        "block": block,
        "event": event,
        "op": op,
        "targetId": target_id,
        "status": status,
        "data": data,
    }


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
    return {
        "name": str(tool.get("name") or "tool"),
        "inputPreview": str(tool.get("inputPreview") or ""),
        "outputPreview": str(tool.get("outputPreview") or ""),
        "outputSize": int(tool.get("outputSize") or 0),
        "outputTruncated": bool(tool.get("outputTruncated") or False),
        "error": str(tool.get("error") or ""),
        "startedAt": str(tool.get("startedAt") or ""),
        "completedAt": str(tool.get("completedAt") or ""),
    }


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


def _collect_generated_files_from_tools(tools: list[dict[str, Any]]) -> list[dict[str, str]]:
    files: list[dict[str, str]] = []
    seen_paths: set[str] = set()
    for tool in tools:
        if str(tool.get("status") or "") != "completed":
            continue
        tool_name = _canonical_tool_name(str(tool.get("name") or ""))
        if tool_name not in {"write", "edit"}:
            continue
        file_path = _extract_file_path_from_tool_input(str(tool.get("inputPreview") or ""))
        if not file_path or file_path in seen_paths:
            continue
        seen_paths.add(file_path)
        files.append(_generated_file_summary(file_path))
    return files


def _canonical_tool_name(name: str) -> str:
    value = name.strip().lower()
    parts = [part for part in value.split("_") if part]
    if len(parts) == 2 and parts[0] == parts[1]:
        return parts[0]
    return value


def _extract_file_path_from_tool_input(input_preview: str) -> str:
    if not input_preview:
        return ""
    decoded = _try_decode_json_object(input_preview)
    if decoded:
        for key in ("file_path", "filePath", "path", "target_file", "targetFile"):
            value = decoded.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return _extract_file_path_from_text(input_preview)


def _try_decode_json_object(value: str) -> dict[str, Any]:
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _extract_file_path_from_text(value: str) -> str:
    for marker in ('"file_path"', "'file_path'", '"filePath"', "'filePath'", '"path"', "'path'"):
        index = value.find(marker)
        if index < 0:
            continue
        tail = value[index + len(marker):]
        colon = tail.find(":")
        if colon < 0:
            continue
        raw = tail[colon + 1:].lstrip()
        if not raw:
            continue
        quote = raw[0] if raw[0] in {'"', "'"} else ""
        if not quote:
            continue
        end = raw.find(quote, 1)
        if end > 1:
            return raw[1:end].strip()
    return ""


def _generated_file_summary(file_path: str) -> dict[str, str]:
    path = PurePosixPath(file_path)
    name = path.name or file_path
    extension = PurePosixPath(name).suffix.lstrip(".").upper() or "FILE"
    return {
        "path": file_path,
        "name": name,
        "extension": extension,
    }


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
