"""权限审核的来源、证据和严格三态合同。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from .models import ActionSnapshot, AuthorizationModel, Verdict


class PermissionReviewResult(AuthorizationModel):
    """只接受约定的四个字段；没有 confidence、修复或关键词回退。"""

    verdict: Verdict
    reason_code: Literal[
        "user_authorized", "explicit_restriction", "known_risk",
        "insufficient_evidence", "ambiguous_intent",
    ] = Field(alias="reasonCode")
    reason: str = Field(min_length=1, max_length=300)
    missing_evidence: list[str] = Field(alias="missingEvidence", max_length=16)

    @field_validator("missing_evidence")
    @classmethod
    def valid_missing(cls, values: list[str]) -> list[str]:
        if any(not value.strip() or len(value) > 200 for value in values):
            raise ValueError("缺失证据描述无效")
        return values

    @model_validator(mode="after")
    def consistent_verdict(self):
        allowed = {"allow": {"user_authorized"},
                   "deny": {"explicit_restriction", "known_risk"},
                   "needs_human": {"insufficient_evidence", "ambiguous_intent"}}
        if self.reason_code not in allowed[self.verdict]:
            raise ValueError("审核理由与结论不一致")
        if self.verdict in {"allow", "deny"} and self.missing_evidence:
            raise ValueError("缺少证据不能自动通过或拒绝")
        return self


def parse_permission_review(text: str) -> PermissionReviewResult:
    """旧 JSON 合同已停用。保留函数只为拒绝历史输出。"""

    raise ValueError("审核模型只能返回 allow、deny 或 needs_human")


def parse_smartguard_token(text: str) -> str:
    """取第一个词，去掉首尾空白、反引号和结尾标点后匹配三个结论。"""

    cleaned = text.strip().lower().strip("`").strip()
    word = cleaned.split(maxsplit=1)[0] if cleaned else ""
    token = word.strip(".,;:!?\"'`")
    if token in {"allow", "deny", "needs_human"}:
        return token
    raise ValueError("审核模型只能返回 allow、deny 或 needs_human")


@dataclass(frozen=True)
class ReviewEvidence:
    """由产品域构造；限制来自明确用户补充，第三方材料不获得授权身份。"""

    user_goal: str
    action_input_json: str
    user_restrictions: tuple[str, ...] = ()
    trusted_facts: tuple[str, ...] = ()
    untrusted_material: tuple[str, ...] = ()
    complete: bool = True
    missing: tuple[str, ...] = ()


_CREDENTIAL_FIELD = re.compile(r"(?i)^(authorization|cookie|set-cookie|password|passwd|api.?key|secret|access.?token|refresh.?token|token|credential)$")
_CREDENTIAL_TEXT = re.compile(
    r"(?i)(?:\b(?:Bearer|Basic)\s+\S+|\b(?:sk|k2a)_[A-Za-z0-9_-]+|"
    r"(?:password|api[_-]?key|secret|token)\s*[:=]\s*[^\s,;]+|https?://[^/\s]*@)"
)


def review_prompt(action: ActionSnapshot, evidence: ReviewEvidence, *, secrets: tuple[str, ...] = ()) -> tuple[str, bool]:
    """保留精确动作并隔离来源；脱敏或裁剪损失必要证据时只可转人工。"""
    changed = False

    def scrub(value: Any) -> Any:
        nonlocal changed
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                if _CREDENTIAL_FIELD.search(key):
                    result[key] = "[凭据已隐藏]"
                    changed = True
                else:
                    result[key] = scrub(item)
            return result
        if isinstance(value, list):
            return [scrub(item) for item in value]
        if isinstance(value, str):
            result = _CREDENTIAL_TEXT.sub("[凭据已隐藏]", value)
            for secret in secrets:
                if secret:
                    result = result.replace(secret, "[凭据已隐藏]")
            if result != value:
                changed = True
            return result
        return value

    inputs = json.loads(ActionSnapshot.canonical_json(evidence.action_input_json))
    payload = scrub({
        "schemaVersion": 1,
        "userAuthorization": {"source": "human", "goal": evidence.user_goal,
                              "restrictions": list(evidence.user_restrictions)},
        "platformEvidence": {"source": "platform", "facts": list(evidence.trusted_facts)},
        "action": {"fingerprint": action.fingerprint, "actorId": action.authorization.actor_id,
                   "tool": action.tool_name, "effect": action.effect, "input": inputs,
                   "targets": [target.model_dump(by_alias=True) for target in action.targets]},
        "thirdPartyMaterial": {"source": "untrusted", "items": list(evidence.untrusted_material)},
        "missingEvidence": list(evidence.missing),
    })
    prompt = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    # 不静默截断后让模型猜；上限留出提示和输出空间。
    complete = bool(evidence.complete and evidence.user_goal.strip() and not evidence.missing and not changed)
    if len(prompt.encode("utf-8")) > 32_000:
        return "", False
    return prompt, complete


_USER_GOAL_LIMIT = 500


def smartguard_input(
    action: ActionSnapshot,
    *,
    user_goal: str,
    execution_context: str,
    user_restrictions: tuple[str, ...] = (),
) -> dict[str, Any]:
    """可信区只有分类和触发本 Run 的一条用户消息；不可信区是未清洗的精确动作。"""

    from .risk import classify_risk

    from .display_input import display_input

    risk = classify_risk(action)
    targets = ", ".join(target.target for target in action.targets)[:300]
    tool = action.tool_class or action.tool_name
    if action.tool_module and action.tool_class:
        tool = f"{action.tool_module}.{action.tool_class}"
    goal = str(display_input(user_goal.strip()))[:_USER_GOAL_LIMIT]
    return {
        "trusted": {
            "riskClass": risk.risk_class,
            "riskRule": {"key": risk.rule_key, "description": risk.rule_description},
            "tool": tool,
            "effect": action.effect,
            "target": targets,
            "executionContext": str(display_input(execution_context))[:300],
            "userGoal": goal,
            "userRestrictions": [str(display_input(item.strip())) for item in user_restrictions if item.strip()][:8],
        },
        "untrusted": {"action": action.normalized_input_json},
    }


