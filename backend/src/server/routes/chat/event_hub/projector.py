"""RunState 投影器。

把底层 AgentScope/RUNTIME_EVENT 原始事件转换成 Chat 前端可直接恢复的轻量状态。
工具结果只保留摘要，完整 Observation 不进入 RunState。
"""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from src.server.routes.chat.event_types import SSE_EVENT_RUNTIME, SSE_EVENT_TEAM
from src.server.routes.chat.lifecycle import (
    can_transition_run_status,
    is_waiting_run_status,
    stage_for_run_status,
    waiting_status_for_interaction,
)
from src.server.routes.chat.run_state_schema import dump_run_state
from src.server.routes.chat.event_hub.display_events import (
    _actor_display_event_for_runtime_event,
    _build_display_events_for_raw_event,
    _display_event,
    _hitl_process_display_events_for_raw_event,
    _hitl_process_item_from_required_event,
    _hitl_resolved_process_display_events,
    _runtime_display_events_for_raw_event,
    _skill_display_events_for_raw_event,
    _subagent_display_event_for_runtime_event,
    _tool_display_events_for_raw_event,
)
from src.server.routes.chat.event_hub.generated_files import (
    generated_files_from_state as _generated_files_from_state,
    normalize_generated_files as _normalize_generated_files,
)
from src.server.routes.chat.event_hub.hitl_display import (
    ask_user_result_preview as _ask_user_result_preview,
    ask_user_tool_call_from_approval as _ask_user_tool_call_from_approval,
    ask_user_tool_call_from_payload as _ask_user_tool_call_from_payload,
    clarification_tool_call_from_approval as _clarification_tool_call_from_approval,
    clarification_tool_call_from_payload as _clarification_tool_call_from_payload,
    format_ask_user_question_preview as _format_ask_user_question_preview,
    is_ask_user_tool_name as _is_ask_user_tool_name,
)
from src.server.routes.chat.event_hub.process_timeline import (
    PROCESS_TIMELINE_KEY,
    _complete_hitl_process_timeline_item,
    _latest_process_text_target_id,
    _project_process_timeline,
    _upsert_hitl_process_timeline_item,
)
from src.server.routes.chat.event_hub.run_state_mutations import (
    _append_model_cache,
    _append_text,
    _final_answer_display_status,
    _finalize_open_tools,
    _find_approval,
    _find_plan,
    _find_tool,
    _has_runtime_event_type,
    _replace_skill_calls,
    _skill_display_event_name,
    _tool_display_data,
    _tool_display_event_name,
    _tool_name,
    _truncate,
    _upsert_approval,
    _upsert_plan,
    _upsert_tool,
)
from src.server.routes.chat.event_hub.runtime_projection import (
    _actor_id_from_runtime_event,
    _build_actor_items_from_timeline,
    _default_actor_id,
    _latest_actor_text_target_id,
    _project_actor_timeline_from_runtime_event,
    _project_subagent_tool_from_runtime_event,
    _runtime_event_has_raw_main_stream_pair,
    _runtime_tool_target_id,
)
from src.server.routes.chat.event_hub.subagent_timeline import (
    _subagent_context_detail,
    is_subagent_runtime_event as _is_subagent_runtime_event,
    model_usage_detail as _model_usage_detail,
)


TOOL_INPUT_PREVIEW_MAX_CHARS = 2_000
TOOL_OUTPUT_PREVIEW_MAX_CHARS = 4_000
REASONING_MAX_CHARS = 100_000
FINAL_ANSWER_MAX_CHARS = 300_000
DISPLAY_SNAPSHOT_VERSION = 3


def initial_run_state(run: Any) -> dict[str, Any]:
    """创建一个新的 RunState 快照。"""

    now = _utc_now()
    return {
        "version": 2,
        "runId": run.id,
        "sessionId": run.session_id,
        "workspaceId": getattr(run, "workspace_id", "default"),
        "projectId": getattr(run, "project_id", "default"),
        "agentId": run.agent_id,
        "agentName": run.agent_name,
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
        "skillCalls": [],
        "modelName": "",
        "tokenUsage": None,
        "plans": [],
        "displaySnapshot": _empty_display_snapshot(),
        "teamSnapshot": None,
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
    elif event_type == "FINAL_ANSWER_REPLACE":
        next_state["finalAnswer"] = _truncate(str(data.get("content") or ""), FINAL_ANSWER_MAX_CHARS)
        next_state["finalAnswerTruncated"] = len(str(data.get("content") or "")) > FINAL_ANSWER_MAX_CHARS
        next_state["currentStage"] = "已生成最终答案"
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
        _upsert_hitl_process_timeline_item(next_state, data)
        waiting_status = waiting_status_for_interaction(
            str(data.get("type") or ""),
            data.get("toolCalls") if isinstance(data.get("toolCalls"), list) else [],
        )
        if can_transition_run_status(str(next_state.get("status") or ""), waiting_status):
            next_state["status"] = waiting_status
            next_state["currentStage"] = stage_for_run_status(waiting_status)
    elif event_type == "CLARIFICATION_REQUIRED":
        _upsert_approval(next_state, data, approval_type="clarification")
        _upsert_hitl_process_timeline_item(next_state, {**data, "kind": "clarification", "type": "clarification"})
        if data.get("clearFinalAnswer"):
            next_state["finalAnswer"] = ""
            next_state["finalAnswerTruncated"] = False
        if can_transition_run_status(str(next_state.get("status") or ""), "waiting_user"):
            next_state["status"] = "waiting_user"
            next_state["currentStage"] = stage_for_run_status(next_state["status"])
    elif event_type == "ERROR":
        if can_transition_run_status(str(next_state.get("status") or ""), "failed"):
            next_state["status"] = "failed"
            next_state["error"] = str(data.get("message") or "执行失败")
            next_state["currentStage"] = "执行失败"
    elif event_type == SSE_EVENT_RUNTIME:
        _project_runtime_event(next_state, data)
    elif event_type == SSE_EVENT_TEAM:
        snapshot = data.get("snapshot") if isinstance(data.get("snapshot"), dict) else None
        if snapshot:
            previous_team_snapshot = (
                next_state.get("teamSnapshot")
                if isinstance(next_state.get("teamSnapshot"), dict)
                else {}
            )
            next_state["teamSnapshot"] = {
                **snapshot,
                "recentEvents": _append_team_event_history(
                    previous_team_snapshot.get("recentEvents"),
                    data,
                ),
            }
            run_state = snapshot.get("run") if isinstance(snapshot.get("run"), dict) else {}
            phase = str(run_state.get("phase") or "")
            next_state["currentStage"] = {
                "created": "创建 TeamRun",
                "coordinating": "Leader 正在组建团队",
                "executing": "成员正在执行共享任务",
                "synthesizing": "Leader 正在综合结果",
                "completed": "TeamRun 已完成",
                "failed": "TeamRun 失败",
            }.get(phase, "TeamRun 运行中")

    if next_state["status"] == "completed":
        next_state["currentStage"] = "已完成"
    elif next_state["status"] == "failed":
        next_state["currentStage"] = "失败"
    elif next_state["status"] == "cancelled":
        next_state["currentStage"] = "已取消"
    elif is_waiting_run_status(str(next_state["status"])):
        next_state["currentStage"] = stage_for_run_status(str(next_state["status"]))
    if next_state["status"] in {"completed", "failed", "cancelled"}:
        _finalize_open_tools(next_state)
    _project_process_timeline(next_state, event_type, data)
    refresh_run_state_display_snapshot(next_state)
    next_state["_displayEvents"] = _build_display_events_for_raw_event(next_state, event_type, data)

    return next_state


def _append_team_event_history(history: Any, event: dict[str, Any]) -> list[dict[str, Any]]:
    """把 Team event 压缩写入可恢复快照，且不递归保存事件内 snapshot。"""

    events = [deepcopy(item) for item in history or [] if isinstance(item, dict)]
    compact = {key: deepcopy(value) for key, value in event.items() if key != "snapshot"}
    event_id = str(compact.get("id") or "")
    event_seq = int(compact.get("seq") or 0)
    if any(
        str(item.get("id") or "") == event_id
        or int(item.get("seq") or 0) == event_seq
        or event_seq in (item.get("payload") or {}).get("mergedSeqs", [])
        for item in events
    ):
        return events[-300:]

    last = events[-1] if events else None
    if (
        isinstance(last, dict)
        and str(compact.get("type") or "").endswith(".delta")
        and last.get("type") == compact.get("type")
        and last.get("actorId") == compact.get("actorId")
        and last.get("taskId") == compact.get("taskId")
    ):
        last_payload = last.get("payload") if isinstance(last.get("payload"), dict) else {}
        compact_payload = compact.get("payload") if isinstance(compact.get("payload"), dict) else {}
        events[-1] = {
            **last,
            "id": compact.get("id"),
            "seq": compact.get("seq"),
            "timestamp": compact.get("timestamp"),
            "payload": {
                **last_payload,
                "delta": f"{last_payload.get('delta') or ''}{compact_payload.get('delta') or ''}",
                "mergedSeqs": [
                    *(last_payload.get("mergedSeqs") or [last.get("seq")]),
                    compact.get("seq"),
                ],
            },
        }
    else:
        events.append(compact)
    return events[-300:]


def _empty_display_snapshot() -> dict[str, Any]:
    return {
        "version": DISPLAY_SNAPSHOT_VERSION,
        "mode": "single_agent",
        "actors": [],
        "blocks": [],
    }


def _build_display_snapshot(state: dict[str, Any]) -> dict[str, Any]:
    status = str(state.get("status") or "running")
    display_status = "waiting" if is_waiting_run_status(status) else status
    updated_at = str(state.get("updatedAt") or _utc_now())
    actors = _build_display_actors(state, display_status, updated_at)
    blocks: list[dict[str, Any]] = []
    tools = [tool for tool in state.get("tools") or [] if isinstance(tool, dict)]

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
            "status": _final_answer_display_status(display_status),
            "updatedAt": updated_at,
            "items": [],
            "data": {
                "content": final_answer,
                "started": True,
            },
        })

    generated_files = _generated_files_from_state(state, tools)
    if status == "completed" and generated_files:
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
        "mode": "single_agent",
        "actors": actors,
        "blocks": blocks,
    }


def _build_display_actors(state: dict[str, Any], status: str, updated_at: str) -> list[dict[str, Any]]:
    """构建单 Agent actor 列表。"""

    return [_display_actor_from_single_agent(state, status, updated_at)]


def _display_actor_from_single_agent(state: dict[str, Any], status: str, updated_at: str) -> dict[str, Any]:
    """把单 Agent RunState 转成统一 actor。"""

    actor_id = _default_actor_id(state)
    return _normalize_display_actor({
        "id": actor_id,
        "role": "single",
        "agentId": str(state.get("agentId") or ""),
        "agentName": str(state.get("agentName") or "Agent"),
        "status": status,
        "task": str(state.get("task") or ""),
        "detail": str(state.get("currentStage") or ""),
        "output": str(state.get("finalAnswer") or ""),
        "createdAt": str(state.get("createdAt") or updated_at),
        "updatedAt": updated_at,
        "completedAt": str(state.get("completedAt") or ""),
        "items": _build_actor_process_items(state, status, updated_at),
    })


def _normalize_display_actor(actor: dict[str, Any]) -> dict[str, Any]:
    """补齐 actor 默认字段，保持前端 schema 稳定。"""

    return {
        "id": str(actor.get("id") or ""),
        "role": "single",
        "agentId": str(actor.get("agentId") or ""),
        "agentName": str(actor.get("agentName") or "Agent"),
        "status": str(actor.get("status") or "running"),
        "task": str(actor.get("task") or ""),
        "detail": str(actor.get("detail") or ""),
        "output": str(actor.get("output") or ""),
        "createdAt": str(actor.get("createdAt") or ""),
        "updatedAt": str(actor.get("updatedAt") or ""),
        "completedAt": str(actor.get("completedAt") or ""),
        "items": actor.get("items") if isinstance(actor.get("items"), list) else [],
        "data": actor.get("data") if isinstance(actor.get("data"), dict) else {},
    }


def _build_actor_process_items(state: dict[str, Any], status: str, updated_at: str) -> list[dict[str, Any]]:
    actor_items = _build_actor_items_from_timeline(state, _default_actor_id(state), updated_at)
    if actor_items:
        return actor_items
    reasoning = str(state.get("reasoning") or "")
    thinking = str(state.get("thinking") or "")
    tools = [tool for tool in state.get("tools") or [] if isinstance(tool, dict)]
    skill_calls = [skill for skill in state.get("skillCalls") or [] if isinstance(skill, dict)]
    return _build_process_items_from_timeline(state, status, updated_at) or _build_process_items(
        reasoning,
        thinking,
        tools,
        skill_calls,
        status,
        updated_at,
    )


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


def _project_runtime_event(state: dict[str, Any], event: dict[str, Any]) -> None:
    runtime_type = str(event.get("type") or "")
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    state["runtimeEvents"] = [*state.get("runtimeEvents", [])[-199:], event]
    _project_actor_timeline_from_runtime_event(state, event)
    _project_subagent_tool_from_runtime_event(state, event)
    if runtime_type == "RUN_WAITING":
        wait_state = str(payload.get("waitState") or event.get("status") or "")
        target_status = wait_state if is_waiting_run_status(wait_state) else "waiting_user"
        if can_transition_run_status(str(state.get("status") or ""), target_status):
            state["status"] = target_status
            state["currentStage"] = stage_for_run_status(state["status"])
    elif runtime_type in {"HITL_RESOLVED", "RUN_RESUMED"}:
        if runtime_type == "RUN_RESUMED" and can_transition_run_status(
            str(state.get("status") or ""),
            "running",
        ):
            state["status"] = "running"
            state["currentStage"] = "继续执行"
        interaction_id = str(payload.get("interactionId") or event.get("interactionId") or "")
        if interaction_id:
            state["approvals"] = [
                {
                    **item,
                    "status": "resolved",
                    "resolvedAt": str(event.get("timestamp") or _utc_now()),
                    "action": str(payload.get("action") or ""),
                    "externalResultsPreview": (
                        payload.get("externalResultsPreview")
                        if isinstance(payload.get("externalResultsPreview"), dict)
                        else {}
                    ),
                }
                if item.get("interactionId") == interaction_id
                else item
                for item in state.get("approvals", [])
            ]
            _complete_hitl_process_timeline_item(state, interaction_id, payload)
    elif runtime_type.startswith("PLAN_"):
        _upsert_plan(state, event)
    elif runtime_type in {"ARTIFACT_CREATED", "ARTIFACT_UPDATED"}:
        files = payload.get("files") if isinstance(payload.get("files"), list) else []
        if files:
            state["generatedFiles"] = _normalize_generated_files(files)
    elif runtime_type in {"RUN_COMPLETED", "RUN_FAILED", "RUN_CANCELLED"}:
        target_status = {
            "RUN_COMPLETED": "completed",
            "RUN_FAILED": "failed",
            "RUN_CANCELLED": "cancelled",
        }[runtime_type]
        if can_transition_run_status(str(state.get("status") or ""), target_status):
            state["status"] = target_status
        if runtime_type == "RUN_CANCELLED":
            state["approvals"] = [
                {
                    **item,
                    "status": "cancelled",
                    "resolvedAt": str(event.get("timestamp") or _utc_now()),
                    "action": "cancelled",
                }
                if isinstance(item, dict) and item.get("status") == "waiting"
                else item
                for item in state.get("approvals", [])
            ]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
