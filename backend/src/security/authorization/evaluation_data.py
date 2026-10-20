"""权限审核人工标注集合同：记录来源和分组，防止调优样本混入 holdout。"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import Field, model_validator

from .models import ActionEffect, AuthorizationModel, Verdict
from .review_contract import ReviewEvidence

Category = Literal["office", "outbound", "restriction", "missing_evidence", "injection", "shell", "multi_actor"]
CATEGORIES = frozenset(Category.__args__)


class EvaluationCase(AuthorizationModel):
    """标签只能由人工提供；groupId 将同一原始案例的变体留在同一分组。"""

    id: str = Field(min_length=1)
    group_id: str = Field(alias="groupId", min_length=1)
    split: Literal["tuning", "holdout"]
    source: str = Field(min_length=1)
    labeled_by: str = Field(alias="labeledBy", min_length=1)
    label_source: Literal["human"] = Field(alias="labelSource")
    categories: list[Category] = Field(min_length=1)
    category: Category | None = None
    suggested_split: Literal["tuning", "holdout"] | None = Field(default=None, alias="suggestedSplit")
    label_note: str = Field(default="", alias="labelNote")
    prelabel_reason: str = Field(default="", alias="prelabelReason")
    label_acceptance: str = Field(default="", alias="labelAcceptance")
    expected: Verdict
    critical: bool
    user_goal: str = Field(alias="userGoal", min_length=1)
    user_restrictions: list[str] = Field(default_factory=list, alias="userRestrictions")
    trusted_facts: list[str] = Field(default_factory=list, alias="trustedFacts")
    untrusted_material: list[str] = Field(default_factory=list, alias="untrustedMaterial")
    tool_name: str = Field(alias="toolName", min_length=1)
    actor_id: str = Field(alias="actorId", min_length=1)
    effect: ActionEffect
    action_input_json: str = Field(alias="actionInputJson")
    complete: bool = True
    missing: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_label(self):
        from .models import ActionSnapshot

        ActionSnapshot.canonical_json(self.action_input_json)
        if self.critical and self.expected == "allow":
            raise ValueError("关键越权案例不能标为 allow")
        if not all(value.strip() for value in (self.id, self.group_id, self.source, self.labeled_by, self.user_goal)):
            raise ValueError("案例来源、人工标注者和目标不能为空白")
        return self

    def evidence(self) -> ReviewEvidence:
        """完整精确动作只用于隔离审核，不执行任何工具。"""
        return ReviewEvidence(self.user_goal, self.action_input_json,
                              tuple(self.user_restrictions), tuple(self.trusted_facts),
                              tuple(self.untrusted_material), self.complete, tuple(self.missing))

    def content_identity(self) -> str:
        """忽略 ID 和标签，禁止复制同一证据跨分组冒充独立案例。"""
        payload = self.model_dump(mode="json", exclude={"id", "group_id", "split", "source", "labeled_by",
                                                         "label_source", "categories", "expected", "critical"})
        payload["action_input_json"] = json.loads(self.action_input_json)
        return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class EvaluationDataset(AuthorizationModel):
    """标注者明确确认 holdout 未用于调 prompt；机器检查分组和重复内容。"""

    schema_version: Literal[1] = Field(alias="schemaVersion")
    holdout_attested_by: str = Field(alias="holdoutAttestedBy", min_length=1)
    holdout_unused_for_tuning: Literal[True] = Field(alias="holdoutUnusedForTuning")
    purpose: str = ""
    how_to_label: dict = Field(default_factory=dict, alias="howToLabel")
    prelabel: dict = Field(default_factory=dict)
    cases: list[EvaluationCase] = Field(min_length=200)

    @model_validator(mode="after")
    def independent_holdout(self):
        if not self.holdout_attested_by.strip():
            raise ValueError("独立测试集需要人工声明")
        ids, groups, identities = set(), {}, set()
        for case in self.cases:
            if case.id in ids or case.content_identity() in identities:
                raise ValueError("案例 ID 或证据重复，不能重复计入 200 条")
            if case.group_id in groups and groups[case.group_id] != case.split:
                raise ValueError("同一原始案例及变体不能跨 tuning/holdout")
            ids.add(case.id)
            identities.add(case.content_identity())
            groups[case.group_id] = case.split
        holdout = [case for case in self.cases if case.split == "holdout"]
        if not any(case.split == "tuning" for case in self.cases):
            raise ValueError("需要明确区分调优集与独立测试集")
        if {category for case in holdout for category in case.categories} != CATEGORIES:
            raise ValueError("独立测试集必须覆盖全部七类权限案例")
        if {case.expected for case in holdout} != {"allow", "deny", "needs_human"}:
            raise ValueError("独立测试集必须包含三个结论的人工标签")
        if not any(case.critical for case in holdout):
            raise ValueError("独立测试集必须包含关键越权案例")
        return self

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.model_dump_json(by_alias=True).encode()).hexdigest()
