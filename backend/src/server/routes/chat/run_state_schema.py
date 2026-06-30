"""Chat RunState v1 schema。

该模块是后端到前端 Chat 主链路的稳定契约。AgentScope raw event、
RuntimeEvent 和 middleware 内部事件都必须先投影成这里定义的结构，
前端不得直接依赖 raw event 作为 live 状态来源。
"""

from typing import Any, Literal

from pydantic import BaseModel, Field


RunStateStatus = Literal["running", "waiting", "completed", "failed", "cancelled", "cancelling"]
RunStatePatchKind = Literal[
    "snapshot_meta",
    "reasoning_delta",
    "final_answer_delta",
    "thinking_delta",
    "tool",
    "team_activities",
    "skill_calls",
    "model_usage",
    "approval",
    "approval_resolved",
    "runtime_event",
    "plans",
    "error",
]


class RunStateToolSummary(BaseModel):
    """RunState 中的轻量工具摘要。"""

    id: str
    name: str = "tool"
    status: str = "running"
    inputPreview: str = ""
    outputPreview: str = ""
    outputSize: int = 0
    outputTruncated: bool = False
    error: str = ""
    startedAt: str = ""
    completedAt: str = ""


class RunStateApprovalSummary(BaseModel):
    """RunState 中的用户介入摘要。"""

    interactionId: str
    kind: str = "hitl"
    type: str = "hitl"
    status: str = "waiting"
    agentId: str = ""
    agentName: str = ""
    toolCalls: list[dict[str, Any]] = Field(default_factory=list)
    title: str = ""
    description: str = ""
    questions: list[dict[str, Any]] = Field(default_factory=list)
    createdAt: str = ""


class RunStatePlanStep(BaseModel):
    """RunState 中的 Plan 结构化步骤。"""

    id: str | None = None
    planId: str | None = None
    stepIndex: int | None = None
    title: str | None = None
    description: str | None = None
    status: str | None = None
    assignedAgentId: str | None = None
    evidence: str | None = None
    result: str | None = None
    startedAt: str | None = None
    completedAt: str | None = None
    updatedAt: str | None = None


class RunStatePlanSummary(BaseModel):
    """RunState 中的 Plan 摘要。"""

    planId: str
    planFile: str = ""
    status: str = "running"
    markdown: str = ""
    steps: list[RunStatePlanStep] = Field(default_factory=list)
    updatedAt: str = ""


class RunStateSnapshot(BaseModel):
    """RunState v1 完整快照。"""

    version: int = 1
    runId: str = ""
    sessionId: str = ""
    agentId: str = ""
    agentName: str = ""
    teamId: str = ""
    teamName: str = ""
    mode: str = "agent"
    status: RunStateStatus | str = "running"
    task: str = ""
    createdAt: str = ""
    updatedAt: str = ""
    completedAt: str | None = None
    currentStage: str = ""
    reasoning: str = ""
    reasoningTruncated: bool = False
    finalAnswer: str = ""
    finalAnswerTruncated: bool = False
    thinking: str = ""
    tools: list[RunStateToolSummary] = Field(default_factory=list)
    teamActivities: list[dict[str, Any]] = Field(default_factory=list)
    skillCalls: list[dict[str, Any]] = Field(default_factory=list)
    modelCaches: list[dict[str, Any]] = Field(default_factory=list)
    modelName: str = ""
    tokenUsage: dict[str, Any] | None = None
    plans: list[RunStatePlanSummary] = Field(default_factory=list)
    approvals: list[RunStateApprovalSummary] = Field(default_factory=list)
    runtimeEvents: list[dict[str, Any]] = Field(default_factory=list)
    error: str = ""
    lastEventType: str = ""
    eventCount: int = 0


class RunStatePatch(BaseModel):
    """RunState v1 增量 patch。"""

    kind: RunStatePatchKind
    runId: str
    sessionId: str
    status: RunStateStatus | str
    currentStage: str = ""
    updatedAt: str = ""
    completedAt: str | None = None
    lastEventType: str = ""
    eventCount: int = 0
    reasoningDelta: str | None = None
    finalAnswerDelta: str | None = None
    thinkingDelta: str | None = None
    tool: RunStateToolSummary | None = None
    teamActivities: list[dict[str, Any]] | None = None
    skillCalls: list[dict[str, Any]] | None = None
    modelCaches: list[dict[str, Any]] | None = None
    modelName: str | None = None
    tokenUsage: dict[str, Any] | None = None
    approval: RunStateApprovalSummary | None = None
    resolvedApprovalId: str | None = None
    runtimeEvent: dict[str, Any] | None = None
    plans: list[RunStatePlanSummary] | None = None
    error: str | None = None


def dump_run_state(state: dict[str, Any]) -> dict[str, Any]:
    """校验并输出 RunState 快照。"""

    return RunStateSnapshot.model_validate(state).model_dump(mode="json")


def dump_run_state_patch(patch: dict[str, Any]) -> dict[str, Any]:
    """校验并输出 RunState patch。"""

    return RunStatePatch.model_validate(patch).model_dump(mode="json", exclude_none=True)
