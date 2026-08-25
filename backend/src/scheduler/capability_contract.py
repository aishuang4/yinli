"""定时任务能力合同。空值视为 generate_only，不得发明通道。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class JobCapabilityClaim(BaseModel):
    """合同认领的一条具体工具。"""

    capability_id: str = Field(alias="capabilityId")
    kind: Literal["builtin", "mcp", "http", "knowledge", "skill"]
    resource_id: str = Field(alias="resourceId")
    server_id: str = Field(default="", alias="serverId")
    tool_name: str = Field(default="", alias="toolName")
    tags: list[str] = Field(default_factory=list)
    unattended_ok: bool = Field(default=True, alias="unattendedOk")
    layer: Literal["required", "optional"] = Field(default="optional")

    model_config = {"populate_by_name": True, "extra": "ignore"}


class JobCapabilityGap(BaseModel):
    """缺失或被权限挡住的意图。"""

    intent: str
    reason: str
    suggested_action: str = Field(default="", alias="suggestedAction")

    model_config = {"populate_by_name": True, "extra": "ignore"}


class JobCapabilityContract(BaseModel):
    """Job 认领过的无人值守能力；空合同 = Phase 1 generate-only。"""

    version: int = 1
    verdict: Literal["ok", "partial", "generate_only"] = "generate_only"
    task_intent: str = Field(default="", alias="taskIntent")
    required_intents: list[str] = Field(default_factory=list, alias="requiredIntents")
    claimed: list[JobCapabilityClaim] = Field(default_factory=list)
    missing: list[JobCapabilityGap] = Field(default_factory=list)
    blocked_by_permission: list[JobCapabilityGap] = Field(
        default_factory=list, alias="blockedByPermission",
    )
    accepted_partial: bool = Field(default=False, alias="acceptedPartial")
    allow_http_request: bool = Field(default=False, alias="allowHttpRequest")
    allow_web_fetch: bool = Field(default=False, alias="allowWebFetch")
    allow_bash: bool = Field(default=False, alias="allowBash")
    skill_ids: list[str] = Field(default_factory=list, alias="skillIds")
    assembled_tool_scope: dict[str, bool] = Field(
        default_factory=lambda: {
            "allowBash": False,
            "allowHttp": False,
            "allowWeb": False,
            "allowMcp": False,
            "allowKnowledge": False,
        },
        alias="assembledToolScope",
    )
    evaluated_at: str = Field(default="", alias="evaluatedAt")
    confirmed_at: str | None = Field(default=None, alias="confirmedAt")

    model_config = {"populate_by_name": True, "extra": "ignore"}

    @field_validator("assembled_tool_scope", mode="before")
    @classmethod
    def _scope_from_any(cls, value: Any) -> dict[str, bool]:
        source = value if isinstance(value, dict) else {}
        return {
            "allowBash": bool(source.get("allowBash", source.get("allow_bash", False))),
            "allowHttp": bool(source.get("allowHttp", source.get("allow_http", False))),
            "allowWeb": bool(source.get("allowWeb", source.get("allow_web", False))),
            "allowMcp": bool(source.get("allowMcp", source.get("allow_mcp", False))),
            "allowKnowledge": bool(
                source.get("allowKnowledge", source.get("allow_knowledge", False)),
            ),
        }

    def is_generate_only(self) -> bool:
        return self.verdict == "generate_only" and not self.claimed

    def to_storage_dict(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, exclude_none=False)


def empty_capability_contract() -> JobCapabilityContract:
    """Phase 1 缺省合同。"""

    return JobCapabilityContract(verdict="generate_only")


def parse_capability_contract(value: Any) -> JobCapabilityContract:
    """把存储 JSON / 缺列读成合同。"""

    if value in (None, "", {}, "null"):
        return empty_capability_contract()
    if isinstance(value, JobCapabilityContract):
        return value
    if isinstance(value, str):
        if not value.strip() or value.strip() in {"{}", "null"}:
            return empty_capability_contract()
    if isinstance(value, dict) and not value:
        return empty_capability_contract()
    return JobCapabilityContract.model_validate(value)
