"""工作室独立审批。只构造 AgentScope 恢复事件，不写入 Chat pending 字典。"""

from __future__ import annotations

import json
import uuid
from typing import Any

from agentscope.event import ConfirmResult, UserConfirmResultEvent
from agentscope.message import ToolCallBlock

from src.group_studio.errors import (
    APPROVAL_NOT_PENDING,
    CROSS_PROJECT_SESSION,
    CROSS_SESSION,
    GROUP_STUDIO_NOT_FOUND,
    STUDIO_PAUSED,
    WORKER_SESSION_LOST,
    GroupStudioError,
)
from src.group_studio.events import emit_envelope
from src.group_studio.models import GroupApproval
from src.group_studio.orchestrator import assert_can_mutate
from src.group_studio.sessions import get_session
from src.storage.database import (
    get_group_studio_approval,
    get_group_studio_snapshot,
    insert_group_studio_approval,
    update_group_studio_approval_status,
    update_group_studio_run_status,
)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _as_tool_call_block(item: dict[str, Any]) -> ToolCallBlock:
    payload = dict(item)
    payload["type"] = "tool_call"
    raw_input = payload.get("input")
    if isinstance(raw_input, dict):
        payload["input"] = json.dumps(raw_input, ensure_ascii=False)
    elif raw_input is None:
        payload["input"] = "{}"
    return ToolCallBlock.model_validate(payload)


def _preview(tool_calls: list[dict[str, Any]]) -> str:
    names = [str(item.get("name") or item.get("toolName") or "tool") for item in tool_calls]
    return ", ".join(names)[:240]


def create_pending_approval(
    *,
    group_id: str,
    run_id: str,
    member_id: str,
    reply_id: str,
    tool_calls: list[dict[str, Any]],
) -> dict[str, Any]:
    """写入一行 pending 审批并发 GROUP_APPROVAL。"""

    row = insert_group_studio_approval({
        "id": _new_id("gapr"),
        "groupId": group_id,
        "runId": run_id,
        "memberId": member_id,
        "replyId": reply_id,
        "toolCalls": tool_calls,
        "argumentsPreview": _preview(tool_calls),
        "status": "pending",
    })
    update_group_studio_run_status(run_id, "waiting_permission")
    emit_envelope(
        group_id=group_id,
        event_type="GROUP_APPROVAL",
        run_id=run_id,
        payload=row,
    )
    return row


def build_confirm_event(*, reply_id: str, tool_calls: list[dict[str, Any]], confirmed: bool) -> UserConfirmResultEvent:
    """构造恢复事件，形状对齐 Chat HITL，但不共用其 pending 表。"""

    return UserConfirmResultEvent(
        reply_id=reply_id,
        confirm_results=[
            ConfirmResult(
                confirmed=confirmed,
                tool_call=_as_tool_call_block(item),
                rules=None,
            )
            for item in tool_calls
        ],
    )


def decide_approval(
    group_id: str,
    *,
    project_id: str,
    approval_id: str,
    decision: str,
) -> GroupApproval:
    """一次决策作用于该 pause 的全部 tool_call。"""

    snapshot = get_group_studio_snapshot(group_id, message_limit=1)
    if snapshot is None:
        raise GroupStudioError(404, GROUP_STUDIO_NOT_FOUND)
    from src.group_studio.access import current_request_session

    session_id = current_request_session()
    owner = str(snapshot.get("chatSessionId") or "")
    if session_id and session_id != owner:
        raise GroupStudioError(422, CROSS_SESSION)
    if not session_id and project_id and project_id != str(snapshot.get("projectId") or ""):
        raise GroupStudioError(422, CROSS_PROJECT_SESSION)
    assert_can_mutate(str(snapshot.get("status") or ""))
    if str(snapshot.get("status") or "") == "paused":
        raise GroupStudioError(409, STUDIO_PAUSED)

    row = get_group_studio_approval(approval_id)
    if row is None or row.get("groupId") != group_id:
        raise GroupStudioError(404, GROUP_STUDIO_NOT_FOUND)
    if row.get("status") != "pending":
        raise GroupStudioError(409, APPROVAL_NOT_PENDING)
    reply_id = str(row.get("replyId") or "").strip()
    if not reply_id:
        raise GroupStudioError(409, APPROVAL_NOT_PENDING)

    session = get_session(group_id, str(row["memberId"]), str(row["runId"]))
    if session is None or session.agent is None:
        raise GroupStudioError(409, WORKER_SESSION_LOST)
    if session.resume is None or session.resume.done():
        raise GroupStudioError(409, WORKER_SESSION_LOST)

    confirmed = decision == "approve"
    event = build_confirm_event(
        reply_id=reply_id,
        tool_calls=list(row.get("toolCalls") or []),
        confirmed=confirmed,
    )
    status = "approved" if confirmed else "denied"
    updated = update_group_studio_approval_status(approval_id, status) or row
    updated["status"] = status
    update_group_studio_run_status(str(row["runId"]), "running")
    emit_envelope(
        group_id=group_id,
        event_type="GROUP_APPROVAL",
        run_id=str(row["runId"]),
        payload=updated,
    )
    session.resume.set_result(event)
    return GroupApproval.model_validate(updated)
