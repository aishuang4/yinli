"""后台 Chat Run 的任务中心摘要。"""

from __future__ import annotations

from typing import Any

from src.server.routes.chat.background_run_events import (
    _base_progress_for_status,
    _parse_sse_event,
    _stage_for_status,
)
from src.server.routes.chat.event_types import SSE_EVENT_RUNTIME, SSE_EVENT_TEAM
from src.server.routes.chat.lifecycle import (
    TERMINAL_RUN_STATUSES,
    is_waiting_run_status,
    stage_for_run_status,
)
from src.storage.database import get_latest_assistant_message_preview


WAITING_EVENT_TYPES = {"HITL_REQUIRED", "CLARIFICATION_REQUIRED"}


def _fill_result_preview_from_history(run: Any, summary: dict) -> dict:
    """当事件摘要没有结果时，从历史 Assistant 消息补充轻量结果预览。"""
    if summary.get("resultPreview"):
        return summary
    latest = get_latest_assistant_message_preview(run.session_id)
    content = str((latest or {}).get("content") or "").strip()
    if not content:
        return summary
    preview = content[:4000]
    return {
        **summary,
        "resultPreview": preview,
        "resultLength": len(content),
    }


def _empty_run_summary(run: Any) -> dict:
    """Build a cheap summary when raw events are intentionally omitted."""
    return {
        "progress": _base_progress_for_status(run.status),
        "currentStage": _stage_for_status(run.status),
        "activeActors": [],
        "resultPreview": "",
        "resultLength": 0,
        "memberTotal": 0,
        "memberCompleted": 0,
    }


def _summarize_run_events(run: Any) -> dict:
    """从底层 SSE 事件提炼任务中心摘要。"""
    runtime_summary = _summarize_runtime_events(run)
    if runtime_summary is not None:
        return runtime_summary

    progress = _base_progress_for_status(run.status)
    current_stage = _stage_for_status(run.status)
    result_parts: list[str] = []
    active_actors: list[str] = []
    total_members = 0
    completed_members = 0

    for raw_event in run.events:
        event_type, data = _parse_sse_event(raw_event)
        if event_type == "MODEL_CALL_START":
            progress = max(progress, 25)
            current_stage = "模型正在思考"
        elif event_type == "TOOL_CALL_START":
            progress = max(progress, 40)
            current_stage = "正在调用工具"
        elif event_type == "TOOL_RESULT_END":
            progress = max(progress, 55)
            current_stage = "工具结果已返回"
        elif event_type == "TEXT_BLOCK_DELTA":
            progress = max(progress, 70)
            result_parts.append(str(data.get("delta") or ""))
            current_stage = "正在生成回复"
        elif event_type == SSE_EVENT_TEAM:
            snapshot = data.get("snapshot") if isinstance(data.get("snapshot"), dict) else {}
            counts = snapshot.get("counts") if isinstance(snapshot.get("counts"), dict) else {}
            members = snapshot.get("members") if isinstance(snapshot.get("members"), list) else []
            total_members = max(0, int(counts.get("members") or len(members)) - 1)
            completed_members = len([
                member for member in members
                if isinstance(member, dict) and member.get("role") != "leader" and member.get("status") == "idle"
            ])
            active_actors = [
                str(member.get("name") or member.get("id"))
                for member in members
                if isinstance(member, dict) and member.get("status") in {"running", "waiting"}
            ]
            task_total = int(counts.get("tasks") or 0)
            task_succeeded = int(counts.get("succeeded") or 0)
            if task_total:
                progress = max(progress, min(90, 20 + int((task_succeeded / task_total) * 65)))
                current_stage = f"Team 协作中：{task_succeeded}/{task_total} 任务完成"
        elif event_type in WAITING_EVENT_TYPES:
            progress = max(progress, 65)
            current_stage = "等待人工处理"
        elif event_type == "REPLY_END":
            progress = max(progress, 90)
            current_stage = "回复已生成，正在收尾"
        elif event_type == "ERROR":
            current_stage = "执行异常"

    if run.status == "completed":
        progress = 100
        current_stage = "已完成"
    elif run.status == "failed":
        progress = 100
        current_stage = "失败"
    elif run.status == "cancelled":
        progress = 100
        current_stage = "已取消"
    elif is_waiting_run_status(run.status):
        current_stage = stage_for_run_status(run.status)

    result_text = "".join(result_parts).strip()
    return {
        "progress": progress,
        "currentStage": current_stage,
        "activeActors": active_actors[:6],
        "resultPreview": result_text,
        "resultLength": len(result_text),
        "memberTotal": total_members,
        "memberCompleted": completed_members,
    }


def _summarize_runtime_events(run: Any) -> dict | None:
    """优先从应用级 Runtime Event 生成任务中心摘要。"""

    runtime_events: list[dict] = []
    result_parts: list[str] = []
    member_status: dict[str, str] = {}
    active_actors: list[str] = []
    current_stage = _stage_for_status(run.status)
    progress = _base_progress_for_status(run.status)

    for raw_event in run.events:
        event_type, data = _parse_sse_event(raw_event)
        if event_type == "TEXT_BLOCK_DELTA":
            result_parts.append(str(data.get("delta") or ""))
        if event_type == SSE_EVENT_TEAM:
            snapshot = data.get("snapshot") if isinstance(data.get("snapshot"), dict) else {}
            members = snapshot.get("members") if isinstance(snapshot.get("members"), list) else []
            counts = snapshot.get("counts") if isinstance(snapshot.get("counts"), dict) else {}
            member_status = {
                str(member.get("id") or ""): str(member.get("status") or "")
                for member in members
                if isinstance(member, dict) and member.get("role") != "leader" and member.get("id")
            }
            active_actors = [
                str(member.get("name") or member.get("id"))
                for member in members
                if isinstance(member, dict) and member.get("status") in {"running", "waiting"}
            ]
            task_total = int(counts.get("tasks") or 0)
            task_succeeded = int(counts.get("succeeded") or 0)
            if task_total:
                progress = max(progress, min(90, 20 + int((task_succeeded / task_total) * 65)))
                current_stage = f"Team 协作中：{task_succeeded}/{task_total} 任务完成"
        if event_type != SSE_EVENT_RUNTIME:
            continue
        runtime_events.append(data)
        runtime_type = str(data.get("type") or "")
        scope = str(data.get("scope") or "")
        agent_name = str(data.get("agentName") or data.get("agentId") or "")
        payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
        if runtime_type == "AGENT_FINAL_ANSWER_DELTA":
            result_parts.append(str(payload.get("delta") or ""))

        if runtime_type == "RUN_WAITING":
            progress = max(progress, 65)
            current_stage = stage_for_run_status(
                str(payload.get("waitState") or data.get("status") or run.status),
            )
        elif runtime_type == "AGENT_MODEL_CALL_STARTED":
            progress = max(progress, 25)
            current_stage = f"{agent_name or 'Agent'} 正在调用模型"
        elif runtime_type == "TOOL_CALL_STARTED":
            progress = max(progress, 40)
            tool_name = str(payload.get("tool_call_name") or "工具")
            current_stage = f"{agent_name or 'Agent'} 正在调用 {tool_name}"
        elif runtime_type == "TOOL_RESULT_COMPLETED":
            progress = max(progress, 55)
            current_stage = "工具结果已返回"
        elif runtime_type in {"AGENT_OUTPUT_DELTA", "AGENT_FINAL_ANSWER_DELTA"}:
            progress = max(progress, 70)
            current_stage = "正在生成回复"
        elif runtime_type == "ARTIFACT_CREATED":
            progress = max(progress, 72)
            current_stage = "已生成文件产物"
        elif runtime_type == "ARTIFACT_UPDATED":
            progress = max(progress, 72)
            current_stage = "已更新文件产物"
        elif runtime_type == "SANDBOX_STARTED":
            progress = max(progress, 15)
            current_stage = "沙盒开始运行"
        elif runtime_type == "SANDBOX_COMPLETED":
            progress = max(progress, 95)
            current_stage = "沙盒运行完成"
        elif runtime_type == "SANDBOX_FAILED":
            progress = 100
            current_stage = "沙盒运行失败"
        if scope in {"leader", "single", "single_agent"} and agent_name and str(data.get("status") or "") in {"running", "waiting"}:
            if agent_name not in active_actors:
                active_actors.append(agent_name)

    if not runtime_events:
        return None

    member_total = len(member_status)
    member_completed = len([
        status for status in member_status.values()
        if status in {"completed", "failed"}
    ])
    if member_total:
        progress = max(progress, min(90, 25 + int((member_completed / member_total) * 55)))
        if run.status not in TERMINAL_RUN_STATUSES and not is_waiting_run_status(run.status):
            current_stage = f"Member 协作中：{member_completed}/{member_total} 完成"

    if run.status == "completed":
        progress = 100
        current_stage = "已完成"
    elif run.status == "failed":
        progress = 100
        current_stage = "失败"
    elif run.status == "cancelled":
        progress = 100
        current_stage = "已取消"
    elif is_waiting_run_status(run.status):
        current_stage = stage_for_run_status(run.status)

    result_text = "".join(result_parts).strip()
    return {
        "progress": progress,
        "currentStage": current_stage,
        "activeActors": active_actors[:6],
        "resultPreview": result_text,
        "resultLength": len(result_text),
        "memberTotal": member_total,
        "memberCompleted": member_completed,
    }
