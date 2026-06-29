"""Plan Mode 审批端点。"""

from fastapi import HTTPException
from pydantic import BaseModel

from src.server.routes.chat.plan_mode.approval import (
    PlanApprovalError,
    approve_plan,
    deny_plan,
)
from src.server.routes.chat.router import router


class PlanDenyRequest(BaseModel):
    """拒绝 Plan 的请求体。"""

    reason: str = ""


@router.post("/plans/{plan_id}/approve")
async def approve_chat_plan(plan_id: str) -> dict:
    """批准 Plan，下一轮 Chat 可通过 approvedPlanId 进入 Build mode。"""

    try:
        return approve_plan(plan_id)
    except PlanApprovalError as exc:
        _raise_plan_approval_http_error(exc)


@router.post("/plans/{plan_id}/deny")
async def deny_chat_plan(plan_id: str, data: PlanDenyRequest) -> dict:
    """拒绝 Plan，并记录拒绝原因。"""

    try:
        return deny_plan(plan_id, reason=data.reason)
    except PlanApprovalError as exc:
        _raise_plan_approval_http_error(exc)


def _raise_plan_approval_http_error(exc: PlanApprovalError) -> None:
    message = str(exc)
    if "not found" in message.lower():
        raise HTTPException(status_code=404, detail=message) from exc
    raise HTTPException(status_code=409, detail=message) from exc
