"""Leader 流中的 Plan 工具事件映射与最终答案解析。"""

from __future__ import annotations

import json

from src.server.routes.chat.models import ChatToolRun
from src.server.routes.chat.runtime_events import runtime_trace_source_event
from src.server.routes.chat.projection.source_events import SourceEvent


PLAN_TOOL_RUNTIME_EVENTS = {
    "plan_enter": ("PLAN_MODE_ENTERED", "running"),
    "plan_write": ("PLAN_UPDATED", "running"),
    "plan_exit": ("PLAN_FINALIZED", "completed"),
    "todo_write": ("PLAN_TASKS_UPDATED", "running"),
    "task_output": ("PLAN_TASK_OUTPUT", ""),
}


def plan_runtime_event_from_tool_result(
    *,
    tool_calls: list[ChatToolRun],
    runtime_events: list[dict],
    event_type: str,
    event_data: dict,
    session_id: str,
    agent_id: str,
    agent_name: str,
) -> SourceEvent | None:
    """从 Plan 控制工具结果兜底生成实时 Runtime Event。"""

    if event_type != "TOOL_RESULT_END":
        return None
    tool_call_id = str(event_data.get("tool_call_id") or "")
    tool_call = next((item for item in tool_calls if item.id == tool_call_id), None)
    if not tool_call:
        return None
    tool_name = tool_call.name
    runtime_info = PLAN_TOOL_RUNTIME_EVENTS.get(tool_name)
    if not runtime_info:
        return None
    try:
        result = json.loads(tool_call.result or "{}")
    except json.JSONDecodeError:
        return None
    if not isinstance(result, dict):
        return None

    plan_id = str(result.get("planId") or "")
    if not plan_id:
        return None
    runtime_type, default_status = runtime_info
    if _has_existing_plan_tool_runtime_event(
        runtime_events,
        runtime_type=runtime_type,
        plan_id=plan_id,
        tool_call_id=tool_call_id,
        result=result,
    ):
        return None

    payload = {
        "planId": plan_id,
        "planFile": str(result.get("planFile") or ""),
        "toolCallId": tool_call_id,
    }
    if tool_name == "plan_enter":
        payload["mode"] = "plan"
    for key in ("markdown", "steps", "step"):
        if key in result:
            payload[key] = (
                str(result.get(key) or "")
                if key == "markdown"
                else result.get(key) or ([] if key == "steps" else {})
            )
    status = default_status or _runtime_status_from_plan_tool_result(result)
    return runtime_trace_source_event(
        runtime_type,
        session_id=session_id,
        scope="plan",
        source="runtime",
        source_event_type=event_type,
        agent_id=agent_id,
        agent_name=agent_name,
        status=status,
        reason="Plan finalized as final answer." if runtime_type == "PLAN_FINALIZED" else "",
        payload=payload,
        collect=runtime_events,
    )


def plan_final_answer_from_sse(event: SourceEvent) -> str:
    """从 PLAN_FINALIZED 结构化源信号提取最终答复文本。"""

    if event.event_type != "PLAN_FINALIZED":
        return ""
    return plan_final_answer_from_runtime_event(event.data)


def plan_final_answer_from_runtime_event(runtime_event: dict) -> str:
    """从 PLAN_FINALIZED RuntimeEvent 中提取计划 markdown。"""

    payload = runtime_event.get("payload") if isinstance(runtime_event.get("payload"), dict) else {}
    return str(payload.get("markdown") or "").strip()


def iter_plan_final_answer_deltas(text: str, chunk_size: int = 24) -> list[str]:
    """把 Plan 最终答案拆成适合前端逐步渲染的文本片段。"""

    if not text:
        return []
    chunks: list[str] = []
    buffer = ""
    for char in text:
        buffer += char
        if char == "\n" or len(buffer) >= chunk_size:
            chunks.append(buffer)
            buffer = ""
    if buffer:
        chunks.append(buffer)
    return chunks


def _has_existing_plan_tool_runtime_event(
    runtime_events: list[dict],
    *,
    runtime_type: str,
    plan_id: str,
    tool_call_id: str,
    result: dict,
) -> bool:
    """判断工具结果是否已经由 manager 原生事件实时发送过。"""

    result_step_id = str((result.get("step") or {}).get("id") or "")
    result_steps = result.get("steps") if isinstance(result.get("steps"), list) else None
    for event in runtime_events:
        payload = event.get("payload", {})
        if event.get("type") != runtime_type or str(payload.get("planId") or "") != plan_id:
            continue
        if tool_call_id and str(payload.get("toolCallId") or "") == tool_call_id:
            return True
        if payload.get("toolCallId"):
            continue
        if runtime_type == "PLAN_TASK_OUTPUT":
            if result_step_id and str((payload.get("step") or {}).get("id") or "") == result_step_id:
                return True
            continue
        if runtime_type == "PLAN_TASKS_UPDATED":
            if result_steps is None:
                return True
            payload_steps = payload.get("steps")
            if isinstance(payload_steps, list) and json.dumps(
                payload_steps, sort_keys=True, default=str
            ) == json.dumps(result_steps, sort_keys=True, default=str):
                return True
            continue
        return True
    return False


def _runtime_status_from_plan_tool_result(result: dict) -> str:
    status = str((result.get("step") or {}).get("status") or "").lower()
    if status == "failed":
        return "failed"
    if status in {"completed", "skipped"}:
        return "completed"
    return "running"
