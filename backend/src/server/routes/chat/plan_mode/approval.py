"""Plan Mode 审批服务。

本模块只处理计划记录状态流转和 Runtime Event payload 构造；HTTP 路由和
ReAct 执行层都复用这里的纯函数，避免恢复旧 Plan Runtime。
"""

from datetime import datetime, timezone
from typing import Any

from src.server.routes.chat.plan_mode.events import build_plan_event
from src.server.routes.chat.plan_mode.models import (
    PLAN_STATUS_APPROVED,
    PLAN_STATUS_DENIED,
    PLAN_STATUS_WAITING_APPROVAL,
    PlanModeContext,
)
from src.storage.database import (
    append_plan_event_record,
    get_plan_record,
    update_plan_record,
)


class PlanApprovalError(ValueError):
    """Plan 审批失败。"""


def approve_plan(plan_id: str) -> dict[str, Any]:
    """批准等待中的 Plan，并返回前端可消费的 Runtime Event。"""

    plan = _require_waiting_plan(plan_id)
    updated = update_plan_record(plan_id, status=PLAN_STATUS_APPROVED)
    append_plan_event_record(
        plan_id,
        step_id=None,
        event_type="PLAN_APPROVED",
        content="Plan approved by user.",
    )
    next_plan = updated or {**plan, "status": PLAN_STATUS_APPROVED}
    return {
        "plan": next_plan,
        "runtimeEvent": _build_approval_event(next_plan, "PLAN_APPROVED", "completed", {}),
    }


def deny_plan(plan_id: str, *, reason: str = "") -> dict[str, Any]:
    """拒绝等待中的 Plan，并返回前端可消费的 Runtime Event。"""

    plan = _require_waiting_plan(plan_id)
    denial_reason = reason.strip() or "用户拒绝了该计划。"
    completed_at = datetime.now(timezone.utc).isoformat()
    updated = update_plan_record(
        plan_id,
        status=PLAN_STATUS_DENIED,
        completed_at=completed_at,
    )
    append_plan_event_record(
        plan_id,
        step_id=None,
        event_type="PLAN_DENIED",
        content=f"Plan denied by user: {denial_reason}",
    )
    next_plan = updated or {
        **plan,
        "status": PLAN_STATUS_DENIED,
        "completedAt": completed_at,
    }
    return {
        "plan": next_plan,
        "runtimeEvent": _build_approval_event(
            next_plan,
            "PLAN_DENIED",
            "failed",
            {"reason": denial_reason},
        ),
    }


def _require_waiting_plan(plan_id: str) -> dict[str, Any]:
    plan = get_plan_record(plan_id)
    if plan is None:
        raise PlanApprovalError("Plan not found.")
    if plan.get("status") != PLAN_STATUS_WAITING_APPROVAL:
        raise PlanApprovalError("Plan is not waiting for approval.")
    return plan


def _build_approval_event(
    plan: dict[str, Any],
    event_type: str,
    status: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    context = PlanModeContext(
        session_id=str(plan.get("sessionId") or ""),
        run_id=str(plan.get("runId") or ""),
        agent_id=str(plan.get("agentId") or ""),
        agent_name="",
        plan_id=str(plan.get("id") or ""),
        current_plan_file=_plan_file_path(plan),
        markdown=str(plan.get("markdown") or ""),
    )
    return build_plan_event(
        event_type,
        context=context,
        status=status,
        payload=payload,
    )


def _plan_file_path(plan: dict[str, Any]) -> str:
    session_id = str(plan.get("sessionId") or "").replace("/", "-")
    plan_id = str(plan.get("id") or "")
    return f".agent-workstation/plans/{session_id}/{plan_id}.md"
