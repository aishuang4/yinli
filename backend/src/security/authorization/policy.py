"""边界先于影响与模式；平台开放模式也不能覆盖明确硬限制。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .models import ActionSnapshot, DecisionSource, RiskClass, Verdict
from .risk import classify_risk


@dataclass(frozen=True)
class PolicyResult:
    """边界与模式判断；智能审核由 service 独立调用。"""

    verdict: Verdict
    source: DecisionSource
    reason_code: str
    reason: str
    review_required: bool = False
    risk_class: RiskClass = "unknown"
    risk_rule_key: str = ""
    risk_rule_description: str = ""


BoundaryCheck = Callable[[ActionSnapshot], Awaitable[str | None]]


async def evaluate_policy(action: ActionSnapshot, check_boundary: BoundaryCheck) -> PolicyResult:
    """所有模式都先执行当前租户、能力与执行现场边界检查。"""

    error = await check_boundary(action)
    if error:
        return PolicyResult("deny", "boundary", "hard_boundary", error, risk_class="boundary",
                            risk_rule_key="hard_boundary", risk_rule_description=error)
    risk = classify_risk(action)
    mode = action.authorization.approval_mode
    common = {
        "risk_class": risk.risk_class,
        "risk_rule_key": risk.rule_key,
        "risk_rule_description": risk.rule_description,
    }
    if risk.risk_class in {"trusted_readonly", "platform_internal"}:
        return PolicyResult("allow", "policy", risk.rule_key, risk.rule_description, **common)
    if risk.risk_class == "low_risk" and mode != "manual":
        return PolicyResult("allow", "policy", risk.rule_key, risk.rule_description, **common)
    if mode == "full_access":
        return PolicyResult("allow", "full_access", "full_access", "本轮完全开放，已通过能力边界", **common)
    if risk.risk_class in {"dangerous", "unknown"} and mode == "model_review":
        return PolicyResult("needs_human", "model_error", "review_required", "等待独立智能审核", True, **common)
    return PolicyResult("needs_human", "manual", risk.rule_key or "manual", risk.rule_description or "此行动需要人工批准一次", **common)
