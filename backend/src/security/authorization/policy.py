"""边界先于影响与模式；平台开放模式也不能覆盖明确硬限制。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .models import ActionSnapshot, DecisionSource, Verdict


@dataclass(frozen=True)
class PolicyResult:
    """边界与模式判断；智能审核由 service 独立调用。"""

    verdict: Verdict
    source: DecisionSource
    reason_code: str
    reason: str
    review_required: bool = False


BoundaryCheck = Callable[[ActionSnapshot], Awaitable[str | None]]


async def evaluate_policy(action: ActionSnapshot, check_boundary: BoundaryCheck) -> PolicyResult:
    """所有模式都先执行当前租户、能力与执行现场边界检查。"""

    error = await check_boundary(action)
    if error:
        return PolicyResult("deny", "boundary", "hard_boundary", error)
    if action.effect in {"read_only", "output"}:
        return PolicyResult("allow", "readonly", "trusted_readonly", "可信只读或回复输出")
    mode = action.authorization.approval_mode
    if mode == "full_access":
        return PolicyResult("allow", "full_access", "full_access", "本轮完全开放，已通过能力边界")
    if mode == "manual":
        return PolicyResult("needs_human", "manual", "manual", "此行动需要人工批准一次")
    return PolicyResult("needs_human", "model_error", "review_required", "等待独立智能审核", True)
