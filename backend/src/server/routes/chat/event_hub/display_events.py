"""原始 Chat/Runtime Event 到 DisplayEvent 的转换。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.server.routes.chat.event_types import SSE_EVENT_RUNTIME
from src.server.routes.chat.event_hub.generated_files import (
    generated_files_from_state as _generated_files_from_state,
)
from src.server.routes.chat.event_hub.hitl_display import (
    ask_user_result_preview as _ask_user_result_preview,
    ask_user_tool_call_from_approval as _ask_user_tool_call_from_approval,
    ask_user_tool_call_from_payload as _ask_user_tool_call_from_payload,
    clarification_tool_call_from_approval as _clarification_tool_call_from_approval,
    clarification_tool_call_from_payload as _clarification_tool_call_from_payload,
    format_ask_user_question_preview as _format_ask_user_question_preview,
    hitl_process_item_from_required_event as _hitl_process_item_from_required_event,
)
from src.server.routes.chat.event_hub.process_timeline import _latest_process_text_target_id
from src.server.routes.chat.event_hub.run_state_mutations import (
    _final_answer_display_status,
    _find_approval,
    _find_plan,
    _find_tool,
    _tool_display_data,
    _tool_name,
)
from src.server.routes.chat.event_hub.runtime_projection import (
    _actor_id_from_runtime_event,
    _default_actor_id,
    _latest_actor_text_target_id,
    _runtime_event_has_raw_main_stream_pair,
    _runtime_tool_target_id,
)
from src.server.routes.chat.event_hub.subagent_timeline import (
    is_subagent_runtime_event as _is_subagent_runtime_event,
    model_usage_detail as _model_usage_detail,
)


DISPLAY_EVENT_SCOPE = "single_agent"


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
    if event_type == "FINAL_ANSWER_REPLACE":
        return [_display_event(
            state,
            block="answer",
            event="answer.completed",
            op="replace",
            target_id="final-answer",
            status=_final_answer_display_status(str(state.get("status") or "running")),
            data={"content": str(data.get("content") or ""), "started": True},
        )]
    if event_type in {"TOOL_CALL_START", "TOOL_CALL_DELTA", "TOOL_CALL_END", "TOOL_RESULT_TEXT_DELTA", "TOOL_RESULT_END"}:
        return _tool_display_events_for_raw_event(state, event_type, data)
    if event_type in {"SKILL_CONTEXT", "SKILL_END"}:
        return _skill_display_events_for_raw_event(state, event_type)
    if event_type in {"HITL_REQUIRED", "CLARIFICATION_REQUIRED"}:
        interaction_id = str(data.get("interactionId") or data.get("id") or "")
        approval = _find_approval(state, interaction_id)
        normalized_data = {**data, "kind": "clarification", "type": "clarification"} if event_type == "CLARIFICATION_REQUIRED" else data
        events = []
        if data.get("clearFinalAnswer"):
            events.append(_display_event(
                state,
                block="answer",
                event="answer.completed",
                op="remove",
                target_id="answer",
                status="waiting",
                data={},
            ))
        events.extend(_hitl_process_display_events_for_raw_event(state, normalized_data))
        events.append(
            _display_event(
                state,
                block="feedback",
                event="feedback.waiting",
                op="replace",
                target_id=interaction_id or "feedback",
                status="waiting",
                data={"approval": approval or {}},
            ),
        )
        return events
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
    if _tool_name(data) == "Skill" or str(tool.get("name") or "") == "Skill":
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

    return [_display_event(
        state,
        block="process",
        event=event_name,
        op=op,
        target_id=str(tool.get("id") or tool_id),
        status=str(tool.get("status") or "running"),
        data=event_data,
    )]


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
    subagent_event = _subagent_display_event_for_runtime_event(state, event)
    if subagent_event:
        return [subagent_event]
    actor_event = _actor_display_event_for_runtime_event(state, event)
    if actor_event:
        return [actor_event]
    if runtime_type == "HITL_RESOLVED":
        interaction_id = str(payload.get("interactionId") or event.get("interactionId") or "")
        events = _hitl_resolved_process_display_events(state, interaction_id, payload)
        events.append(_display_event(
            state,
            block="feedback",
            event="feedback.resolved",
            op="remove",
            target_id=interaction_id or "feedback",
            status="completed",
            data={},
        ))
        return events
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
    if runtime_type == "RUN_COMPLETED":
        events: list[dict[str, Any]] = []
        if state.get("finalAnswer"):
            events.append(_display_event(
                state,
                block="answer",
                event="answer.completed",
                op="upsert",
                target_id="final-answer",
                status="completed",
                data={"content": str(state.get("finalAnswer") or ""), "started": True},
            ))
        generated_files = _generated_files_from_state(
            state,
            [item for item in state.get("tools") or [] if isinstance(item, dict)],
        )
        if generated_files:
            events.append(_display_event(
                state,
                block="files",
                event="files.completed",
                op="replace",
                target_id="files",
                status="completed",
                data={"files": generated_files},
            ))
        return events
    return []


def _subagent_display_event_for_runtime_event(state: dict[str, Any], event: dict[str, Any]) -> dict[str, Any] | None:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    runtime_type = str(event.get("type") or "")
    if not _is_subagent_runtime_event(runtime_type, payload):
        return None
    tool_id = str(payload.get("toolCallId") or payload.get("tool_call_id") or "")
    invocation_id = str(payload.get("invocationId") or "")
    if not tool_id and invocation_id:
        tool_id = f"subagent-{invocation_id}"
    if not tool_id:
        return None
    tool = _find_tool(state, tool_id)
    if not tool:
        return None
    status = str(tool.get("status") or "running")
    event_name = "tool.started"
    if status == "completed":
        event_name = "tool.completed"
    elif status == "failed":
        event_name = "tool.failed"
    return _display_event(
        state,
        block="process",
        event=event_name,
        op="upsert",
        target_id=tool_id,
        status=status,
        data=_tool_display_data(tool),
    )


def _actor_display_event_for_runtime_event(state: dict[str, Any], event: dict[str, Any]) -> dict[str, Any] | None:
    """把单 Agent Runtime Event 转成前端 actor process 增量。"""

    if _runtime_event_has_raw_main_stream_pair(event):
        return None
    actor_id = _actor_id_from_runtime_event(state, event)
    if not actor_id:
        return None
    runtime_type = str(event.get("type") or "")
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    status = str(event.get("status") or "running")
    scope = _normalize_display_scope(str(event.get("scope") or ""))

    if runtime_type == "AGENT_OUTPUT_DELTA":
        target_id = _latest_actor_text_target_id(state, actor_id, "reasoning.delta", f"{actor_id}:reasoning")
        return _display_event(
            state,
            block="process",
            event="reasoning.delta",
            op="append",
            target_id=target_id,
            actor_id=actor_id,
            scope=scope,
            status="running",
            data={"text": str(payload.get("delta") or "")},
        )
    if runtime_type == "AGENT_THINKING":
        target_id = _latest_actor_text_target_id(state, actor_id, "thinking.delta", f"{actor_id}:thinking")
        return _display_event(
            state,
            block="process",
            event="thinking.delta",
            op="append",
            target_id=target_id,
            actor_id=actor_id,
            scope=scope,
            status="running",
            data={"text": str(payload.get("delta") or "")},
        )
    if runtime_type in {
        "TOOL_CALL_STARTED",
        "TOOL_CALL_DELTA",
        "TOOL_CALL_COMPLETED",
        "TOOL_RESULT_STARTED",
        "TOOL_RESULT_DELTA",
        "TOOL_RESULT_COMPLETED",
    }:
        tool_id = _runtime_tool_target_id(actor_id, payload)
        if not tool_id:
            return None
        tool_name = str(payload.get("tool_call_name") or "tool")
        if tool_name == "Skill":
            return None
        if runtime_type == "TOOL_CALL_DELTA":
            return _display_event(
                state,
                block="process",
                event="tool.input.delta",
                op="append",
                target_id=tool_id,
                actor_id=actor_id,
                scope=scope,
                status="running",
                data={"name": tool_name, "inputPreview": str(payload.get("delta") or "")},
            )
        if runtime_type == "TOOL_RESULT_DELTA":
            return _display_event(
                state,
                block="process",
                event="tool.output.delta",
                op="append",
                target_id=tool_id,
                actor_id=actor_id,
                scope=scope,
                status="running",
                data={"name": tool_name, "outputPreview": str(payload.get("delta") or "")},
            )
        item_status = "running"
        event_name = "tool.started"
        if runtime_type == "TOOL_RESULT_COMPLETED":
            item_status = "failed" if status == "failed" else "completed"
            event_name = "tool.failed" if item_status == "failed" else "tool.completed"
        return _display_event(
            state,
            block="process",
            event=event_name,
            op="upsert",
            target_id=tool_id,
            actor_id=actor_id,
            scope=scope,
            status=item_status,
            data={
                "name": tool_name,
                "inputPreview": "",
                "outputPreview": "",
                "outputSize": 0,
                "outputTruncated": False,
                "error": str(payload.get("state") or "") if item_status == "failed" else "",
            },
        )
    return None


def _hitl_process_display_events_for_raw_event(state: dict[str, Any], data: dict[str, Any]) -> list[dict[str, Any]]:
    item = _hitl_process_item_from_required_event(state, data)
    if not item:
        return []
    return [_display_event(
        state,
        block="process",
        event=str(item.get("event") or "tool.started"),
        op="upsert",
        target_id=str(item.get("targetId") or item.get("id") or ""),
        actor_id=_hitl_actor_id(state, data),
        scope=_normalize_display_scope(str(data.get("scope") or "")),
        status=str(item.get("status") or "waiting"),
        data=item.get("data") if isinstance(item.get("data"), dict) else {},
    )]


def _hitl_resolved_process_display_events(
    state: dict[str, Any],
    interaction_id: str,
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    approval = _find_approval(state, interaction_id)
    tool_call = _ask_user_tool_call_from_approval(approval)
    if not tool_call:
        tool_call = _clarification_tool_call_from_approval(approval)
    tool_id = str(tool_call.get("id") or "")
    if not tool_id:
        return []
    status = "completed" if str(payload.get("action") or "") == "approve" else "failed"
    output_preview = _ask_user_result_preview(tool_id, payload)
    if status == "completed" and not output_preview:
        return []
    return [_display_event(
        state,
        block="process",
        event="tool.completed" if status == "completed" else "tool.failed",
        op="upsert",
        target_id=tool_id,
        actor_id=_hitl_actor_id_from_approval(state, approval),
        scope=_normalize_display_scope(str((approval or {}).get("scope") or "")),
        status=status,
        data={
            "name": "AskUser",
            "inputPreview": _format_ask_user_question_preview(tool_call),
            "input": _format_ask_user_question_preview(tool_call),
            "outputPreview": output_preview,
            "output": output_preview,
            "outputSize": len(output_preview),
            "outputTruncated": False,
            "error": "" if status == "completed" else "用户取消或拒绝补充信息。",
        },
    )]


def _hitl_actor_id(state: dict[str, Any], data: dict[str, Any]) -> str:
    return _default_actor_id(state)


def _hitl_actor_id_from_approval(state: dict[str, Any], approval: dict[str, Any] | None) -> str:
    if not isinstance(approval, dict):
        return _default_actor_id(state)
    return _hitl_actor_id(state, approval)


def _display_event(
    state: dict[str, Any],
    *,
    block: str,
    event: str,
    op: str,
    target_id: str,
    status: str,
    data: dict[str, Any],
    actor_id: str | None = None,
    scope: str | None = None,
) -> dict[str, Any]:
    seq = int(state.get("eventCount") or 0)
    created_at = str(state.get("updatedAt") or _utc_now())
    display_event = {
        "id": f"{state.get('runId') or 'run'}-{seq}-{block}-{event}-{target_id}",
        "runId": str(state.get("runId") or ""),
        "sessionId": str(state.get("sessionId") or ""),
        "seq": seq,
        "createdAt": created_at,
        "scope": _normalize_display_scope(scope or _default_display_scope(state)),
        "block": block,
        "event": event,
        "op": op,
        "targetId": target_id,
        "status": status,
        "data": data,
    }
    resolved_actor_id = actor_id or _default_actor_id(state)
    if resolved_actor_id:
        display_event["actorId"] = resolved_actor_id
    return display_event


def _default_display_scope(state: dict[str, Any]) -> str:
    return DISPLAY_EVENT_SCOPE


def _normalize_display_scope(scope: str) -> str:
    """DisplaySnapshot 只接受单 Agent scope。"""

    return DISPLAY_EVENT_SCOPE

def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
