"""平台授权的严格、不可变合同，不依赖 AgentScope 类型。"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ApprovalMode = Literal["manual", "model_review", "full_access"]
OperationScope = Literal["normal", "read_only"]
ActionEffect = Literal["read_only", "mutation", "control_plane_mutation", "outbound", "orchestration", "unknown", "output"]
Verdict = Literal["allow", "deny", "needs_human"]
DecisionSource = Literal["readonly", "manual", "model", "model_error", "boundary", "full_access", "policy"]
RiskClass = Literal[
    "boundary",
    "trusted_readonly",
    "platform_internal",
    "low_risk",
    "human_only",
    "dangerous",
    "unknown",
    "insufficient_evidence",
]


class AuthorizationModel(BaseModel):
    """拒绝未知字段与隐式类型转换，禁止跨轮覆盖快照。"""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, populate_by_name=True)


class RunAuthorizationMetadata(AuthorizationModel):
    """Run 的不可变审批选择；运行现场与连接不得进入普通元数据。"""

    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    tenant_id: str = Field(alias="tenantId", min_length=1)
    approval_mode: ApprovalMode = Field(default="manual", alias="approvalMode")
    reviewer_config_version: str = Field(default="", alias="reviewerConfigVersion")


def validate_approval_mode(value: str) -> ApprovalMode:
    """存储/后台入口也验证枚举，不只依赖 HTTP 请求校验。"""

    if value not in {"manual", "model_review", "full_access"}:
        raise ValueError("审核模式必须是 manual、model_review 或 full_access")
    return value


class RunAuthorization(AuthorizationModel):
    """从服务端构造的 Run/actor 授权归属与策略版本。"""

    tenant_id: str = Field(alias="tenantId", min_length=1)
    workspace_id: str = Field(alias="workspaceId", min_length=1)
    project_id: str = Field(alias="projectId", min_length=1)
    session_id: str = Field(alias="sessionId", min_length=1)
    run_id: str = Field(alias="runId", min_length=1)
    actor_id: str = Field(alias="actorId", min_length=1)
    approval_mode: ApprovalMode = Field(default="manual", alias="approvalMode")
    capability_version: str = Field(alias="capabilityVersion", min_length=1)
    policy_version: Literal[1] = Field(default=1, alias="policyVersion")
    reviewer_config_version: str = Field(default="", alias="reviewerConfigVersion")
    surface: Literal["interactive", "job"] = "interactive"


class TargetVersion(AuthorizationModel):
    """已解析目标及文件摘要/外部 ETag 等版本证据。"""

    target: str = Field(min_length=1)
    version: str = ""
    content_digest: str = Field(default="", alias="contentDigest")


class ResourceRevision(AuthorizationModel):
    """某个行动实际消费的控制面资源修订；不等同于全局能力边界。"""

    resource_type: str = Field(alias="resourceType", min_length=1)
    resource_id: str = Field(alias="resourceId", min_length=1)
    revision: str = Field(min_length=1)


class ActionSnapshot(AuthorizationModel):
    """规范输入用 JSON 字符串保存，避免 frozen 模型内可变字典被篡改。"""

    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    authorization: RunAuthorization
    call_id: str = Field(alias="callId", min_length=1)
    tool_name: str = Field(alias="toolName", min_length=1)
    tool_module: str = Field(default="", alias="toolModule")
    tool_class: str = Field(default="", alias="toolClass")
    tool_version: str = Field(alias="toolVersion", min_length=1)
    normalized_input_json: str = Field(alias="normalizedInputJson")
    targets: tuple[TargetVersion, ...] = ()
    resource_revisions: tuple[ResourceRevision, ...] = Field(default=(), alias="resourceRevisions")
    control_plane_diff: str = Field(default="", alias="controlPlaneDiff", max_length=12000)
    effect: ActionEffect = "unknown"
    restricted_input_ref: str = Field(default="", alias="restrictedInputRef")
    execution_input_digest: str = Field(default="", alias="executionInputDigest")

    @field_validator("normalized_input_json")
    @classmethod
    def canonical_json(cls, value: str) -> str:
        """只接受对象及标准 JSON，确保键顺序不会改变动作身份。"""

        def reject_constant(value: str):
            raise ValueError("动作输入不能包含非有限数值")

        def unique_object(pairs):
            result = {}
            for key, item in pairs:
                if key in result:
                    raise ValueError("动作输入不能包含重复键")
                result[key] = item
            return result

        parsed = json.loads(value, parse_constant=reject_constant, object_pairs_hook=unique_object)
        if not isinstance(parsed, dict):
            raise ValueError("动作输入必须是 JSON 对象")
        return json.dumps(parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)

    @property
    def fingerprint(self) -> str:
        """身份、目标、内容、能力与规范参数全部绑定。"""

        payload = json.dumps(self.model_dump(mode="json", by_alias=True), ensure_ascii=False,
                             sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class AuthorizationDecision(AuthorizationModel):
    """审核结论与真实来源；执行结果另存。"""

    id: str = Field(min_length=1)
    action_fingerprint: str = Field(alias="actionFingerprint", pattern=r"^[a-f0-9]{64}$")
    verdict: Verdict
    source: DecisionSource
    reason_code: str = Field(alias="reasonCode", min_length=1)
    reason: str = Field(min_length=1, max_length=500)
    missing_evidence: tuple[str, ...] = Field(default=(), alias="missingEvidence")
    related_decision_id: str = Field(default="", alias="relatedDecisionId")
    do_not_retry: bool = Field(default=False, alias="doNotRetry")


class ApprovalRecord(AuthorizationModel):
    """绑定一个 decision 的人工批准状态。"""

    id: str = Field(min_length=1)
    decision_id: str = Field(alias="decisionId", min_length=1)
    status: Literal["pending", "approved", "rejected", "cancelled", "expired", "interrupted"]
    approved_by: str = Field(default="", alias="approvedBy")
    expires_at: str = Field(alias="expiresAt", min_length=1)


class ExecutionRecord(AuthorizationModel):
    """一次执行占位及结果，不把批准等同执行成功。"""

    id: str = Field(min_length=1)
    decision_id: str = Field(alias="decisionId", min_length=1)
    status: Literal["reserved", "running", "succeeded", "failed", "unknown"]
