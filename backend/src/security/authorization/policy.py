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

JOB_UNATTENDED_DENIAL = "定时任务无人值守，该工具已拒绝。换一种做法。"
_JOB_CONTRACT_TOOLS = frozenset({
    "HttpRequest", "WebFetch", "ConfiguredHttpTool", "KnowledgeBaseSearch", "MCPTool",
})
_JOB_SHELL_HARD_FLOOR = frozenset({
    "recursive_delete", "sudo", "force_push", "pipe_to_shell", "chmod_777", "system_path_write",
    "shell_unparsed", "launchctl", "kill_app", "systemctl_stop",
})


def _job_hard_floor(risk) -> bool:
    """定时任务的硬底线。完全开放也不能绕过。"""

    return risk.risk_class == "human_only" or risk.rule_key in _JOB_SHELL_HARD_FLOOR


def _job_contract_allowed(action: ActionSnapshot, risk) -> bool:
    """冻结合同里的工具。manual 下也执行。"""

    if risk.risk_class == "low_risk":
        return True
    name = action.tool_class or action.tool_name
    return name in _JOB_CONTRACT_TOOLS


def _evaluate_job_policy(action: ActionSnapshot, risk, mode: str, common: dict) -> PolicyResult:
    """定时运行只返回 allow 或 deny，不产生 needs_human，也不调用智能审核。"""

    if _job_hard_floor(risk):
        return PolicyResult(
            "deny", "policy", risk.rule_key or "hard_floor",
            risk.rule_description or "定时任务硬底线拒绝", **common,
        )
    if _job_contract_allowed(action, risk):
        return PolicyResult(
            "allow", "policy", risk.rule_key or "unattended_contract",
            risk.rule_description or "定时任务已确认的工具", **common,
        )
    if mode == "full_access":
        return PolicyResult("allow", "full_access", "full_access", "本轮完全开放，已通过能力边界", **common)
    return PolicyResult("deny", "policy", "unattended_denied", JOB_UNATTENDED_DENIAL, **common)


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
    if action.authorization.surface == "job":
        return _evaluate_job_policy(action, risk, mode, common)
    if risk.risk_class == "low_risk" and mode != "manual":
        return PolicyResult("allow", "policy", risk.rule_key, risk.rule_description, **common)
    if mode == "full_access":
        return PolicyResult("allow", "full_access", "full_access", "本轮完全开放，已通过能力边界", **common)
    if risk.risk_class in {"dangerous", "unknown"} and mode == "model_review":
        return PolicyResult("needs_human", "model_error", "review_required", "等待独立智能审核", True, **common)
    return PolicyResult("needs_human", "manual", risk.rule_key or "manual", risk.rule_description or "此行动需要人工批准一次", **common)
