"""Team activity 纯状态操作、工具调用记录更新与成员依赖工具。"""

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from src.server.routes.chat.helpers import _trim_text
from src.server.routes.chat.models import (
    ChatTeamActivity,
    ChatTeamMemberPayload,
    ChatToolRun,
)
from src.server.routes.chat.prompts import build_worker_assignment_text
from src.server.routes.chat.store import _make_session_title


def _tool_result_status(event_data: dict) -> str:
    state = str(event_data.get("state") or "").lower()
    return "failed" if state in {"error", "failed"} else "completed"


def _stringify_tool_input(value: Any) -> str:
    """Serialize AgentScope tool input for chat history display."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except TypeError:
        return str(value)


def _pending_tool_calls_to_runs(
    pending_tool_calls: list[dict],
    existing_tool_calls: list[ChatToolRun] | None = None,
) -> list[ChatToolRun]:
    """Convert paused HITL tool calls into persisted chat tool records."""
    next_calls = [item.model_copy() for item in existing_tool_calls or []]
    seen_ids = {item.id for item in next_calls}
    for tool_call in pending_tool_calls:
        tool_call_id = str(tool_call.get("id") or "")
        if not tool_call_id or tool_call_id in seen_ids:
            continue
        next_calls.append(ChatToolRun(
            id=tool_call_id,
            name=str(tool_call.get("name") or "tool"),
            args=_stringify_tool_input(tool_call.get("input")),
            status="running",
        ))
        seen_ids.add(tool_call_id)
    return next_calls


def _collect_worker_outputs(activities: list[ChatTeamActivity]) -> list[dict[str, str]]:
    """Collect formal Worker reports for the leader."""
    return [
        {
            "agentId": activity.agent_id,
            "name": activity.agent_name,
            "status": activity.status,
            "detail": activity.detail,
            "report": activity.report,
            "output": activity.report,
            "skippedReason": activity.skipped_reason,
            "reportReviewStatus": activity.report_review_status,
            "reportReviewNotes": activity.report_review_notes,
            "reportReviewGaps": "\n".join(activity.report_review_gaps),
        }
        for activity in activities
        if activity.role != "leader"
        and activity.status in {"completed", "failed", "waiting"}
        and (activity.report or activity.status in {"failed", "waiting"})
    ]


def _build_team_activities(
    *,
    team_name: str | None,
    leader_agent_id: str,
    leader_agent_name: str,
    members: list[ChatTeamMemberPayload],
    user_message: str,
) -> list[ChatTeamActivity]:
    """Create UI-visible Team activity records for the current run."""
    if not team_name and not members:
        return []

    now = datetime.now(timezone.utc).isoformat()
    summary = _make_session_title(user_message)
    activities = [
        ChatTeamActivity(
            id=f"team-{uuid.uuid4().hex}",
            agentId=leader_agent_id,
            agentName=leader_agent_name,
            role="leader",
            status="running",
            task=f"拆解任务并汇总 Team: {team_name or 'Unnamed Team'}",
            detail=f"正在组织成员围绕「{summary}」协作，并生成最终回复。",
            executionMode="leader",
            createdAt=now,
        )
    ]

    for member in members:
        if member.role == "leader" or member.agent_id == leader_agent_id:
            continue
        status = "running" if member.role == "worker" else "pending"
        assignment = build_worker_assignment_text(member)
        activities.append(ChatTeamActivity(
            id=f"team-{uuid.uuid4().hex}",
            agentId=member.agent_id,
            agentName=member.name,
            role=member.role,
            status=status,
            task=_make_member_task(member, summary),
            assignment=assignment,
            expectedOutput="返回可供 Leader 汇总的 TeamSay-style report。",
            constraints="不要直接生成最终团队答案，只报告你的专业发现、证据和风险。",
            report="",
            skippedReason="",
            reportReviewStatus="",
            reportReviewNotes="",
            reportReviewGaps=[],
            detail=assignment or "等待 Leader 协调上下文。",
            output="",
            thinking="",
            executionMode=member.execution_mode,
            dependsOn=member.depends_on,
            waitReason=(
                f"等待依赖 Worker 完成：{', '.join(member.depends_on)}"
                if member.execution_mode == "depends_on" and member.depends_on
                else ""
            ),
            createdAt=now,
        ))
    return activities


def _make_member_task(member: ChatTeamMemberPayload, summary: str) -> str:
    """Generate a compact task label for a Team member."""
    if member.role == "leader":
        return f"作为 Leader 汇总「{summary}」"
    return f"{build_worker_assignment_text(member)} 任务：「{summary}」"


def _set_team_activity_status(
    activities: list[ChatTeamActivity],
    status: str,
) -> list[ChatTeamActivity]:
    """Return Team activities with a unified terminal status."""
    return [
        item.model_copy(update={"status": status})
        for item in activities
    ]


def _finalize_team_activities(
    activities: list[ChatTeamActivity],
    *,
    leader_agent_id: str,
    leader_output: str,
) -> list[ChatTeamActivity]:
    """Mark normal Team activities complete while preserving failed/waiting states."""
    finalized: list[ChatTeamActivity] = []
    for item in activities:
        if item.status in {"failed", "waiting"}:
            finalized.append(item)
            continue
        if item.agent_id == leader_agent_id or item.role == "leader":
            finalized.append(item.model_copy(update={
                "status": "completed",
                "detail": "Leader 已完成最终汇总。",
                "output": leader_output,
            }))
        else:
            finalized.append(item.model_copy(update={"status": "completed"}))

    activities[:] = finalized
    return finalized


def _mark_team_leader_waiting_for_clarification(
    activities: list[ChatTeamActivity],
    leader_agent_id: str,
) -> None:
    """Keep Team state non-terminal when the leader needs user input."""
    if not activities:
        return
    updated: list[ChatTeamActivity] = []
    for item in activities:
        if item.agent_id == leader_agent_id or item.role == "leader":
            updated.append(item.model_copy(update={
                "status": "waiting",
                "detail": "Leader 需要用户补充信息后继续。",
            }))
        else:
            updated.append(item)
    activities[:] = updated


def _set_team_worker_status(
    activities: list[ChatTeamActivity],
    status: str,
) -> list[ChatTeamActivity]:
    """Return Team activities with all worker records moved to a new status."""
    return [
        item.model_copy(update={"status": status})
        if item.role != "leader"
        else item
        for item in activities
    ]


def _update_worker_tool_calls(
    tool_calls: list[ChatToolRun],
    event_type: str,
    event_data: dict,
) -> list[ChatToolRun]:
    """Update worker tool call records from AgentScope tool events."""
    tool_call_id = str(event_data.get("tool_call_id") or "")
    if not tool_call_id:
        return tool_calls

    next_calls = [item.model_copy() for item in tool_calls]
    index = next((idx for idx, item in enumerate(next_calls) if item.id == tool_call_id), -1)

    if event_type == "TOOL_CALL_START":
        if index == -1:
            next_calls.append(ChatToolRun(
                id=tool_call_id,
                name=str(event_data.get("tool_call_name") or "tool"),
                status="calling",
            ))
        return next_calls

    if index == -1:
        next_calls.append(ChatToolRun(
            id=tool_call_id,
            name=str(event_data.get("tool_call_name") or "tool"),
            status="calling",
        ))
        index = len(next_calls) - 1

    current = next_calls[index]
    if event_type == "TOOL_CALL_DELTA":
        next_calls[index] = current.model_copy(update={
            "args": current.args + str(event_data.get("delta") or ""),
        })
    elif event_type in {"TOOL_CALL_END", "TOOL_RESULT_START"}:
        next_calls[index] = current.model_copy(update={"status": "running"})
    elif event_type == "TOOL_RESULT_TEXT_DELTA":
        next_calls[index] = current.model_copy(update={
            "result": current.result + str(event_data.get("delta") or ""),
            "status": "running",
        })
    elif event_type == "TOOL_RESULT_END":
        next_calls[index] = current.model_copy(update={"status": _tool_result_status(event_data)})
    else:
        return tool_calls

    return next_calls


def _find_team_activity(
    activities: list[ChatTeamActivity],
    agent_id: str,
) -> ChatTeamActivity | None:
    """Find one Team activity by Agent id."""
    return next((item for item in activities if item.agent_id == agent_id), None)


def _replace_team_activity(
    activities: list[ChatTeamActivity],
    next_activity: ChatTeamActivity,
) -> None:
    """Replace one Team activity in-place."""
    for index, activity in enumerate(activities):
        if activity.id == next_activity.id:
            activities[index] = next_activity
            return


def _member_dependency_ids(
    member: ChatTeamMemberPayload,
    workers: list[ChatTeamMemberPayload],
) -> list[str]:
    """Normalize dependency identifiers to worker agent ids."""
    if member.execution_mode != "depends_on":
        return []

    by_member_id = {getattr(worker, "id", ""): worker.agent_id for worker in workers}
    by_agent_id = {worker.agent_id: worker.agent_id for worker in workers}
    normalized: list[str] = []
    for raw_id in member.depends_on:
        dep_id = by_agent_id.get(raw_id) or by_member_id.get(raw_id) or raw_id
        if dep_id and dep_id != member.agent_id and dep_id not in normalized:
            normalized.append(dep_id)
    return normalized


def _expand_selected_workers_with_dependencies(
    selected_agent_ids: list[str],
    workers: list[ChatTeamMemberPayload],
) -> list[str]:
    """Ensure a selected downstream worker also runs its upstream dependencies."""
    worker_by_agent_id = {worker.agent_id: worker for worker in workers}
    expanded: list[str] = []
    visited: set[str] = set()

    def add_with_dependencies(agent_id: str) -> None:
        if agent_id in visited or agent_id not in worker_by_agent_id:
            return
        visited.add(agent_id)
        member = worker_by_agent_id[agent_id]
        for dep_id in _member_dependency_ids(member, workers):
            add_with_dependencies(dep_id)
        expanded.append(agent_id)

    for agent_id in selected_agent_ids:
        add_with_dependencies(agent_id)

    return expanded


def _member_names(
    agent_ids: list[str],
    workers: list[ChatTeamMemberPayload],
) -> list[str]:
    names = []
    for agent_id in agent_ids:
        worker = next((item for item in workers if item.agent_id == agent_id), None)
        names.append(worker.name if worker else agent_id)
    return names
