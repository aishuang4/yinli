"""Chat RunState v2 / DisplaySnapshot v3 schema。

该模块是后端到前端 Chat 主链路的稳定契约。AgentScope raw event、
RuntimeEvent 和 middleware 内部事件都必须先投影成这里定义的结构，
前端不得直接依赖 raw event 作为 live 状态来源。
"""

from typing import Any, Literal

from pydantic import BaseModel, Field


RunStateStatus = Literal["running", "waiting", "completed", "failed", "cancelled", "cancelling"]
RunStatePatchKind = Literal[
    "snapshot_meta",
    "display_event",
    "model_usage",
    "approval_resolved",
    "notice",
    "error",
]
DisplayBlockKind = Literal["process", "team", "plan", "feedback", "answer", "files", "error"]
DisplayEventOp = Literal["append", "upsert", "replace", "remove", "clear"]
DisplayMode = Literal["single_agent", "team"]
DisplayScope = Literal["single_agent", "team", "leader", "worker"]


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


class RunStateDisplayItem(BaseModel):
    """DisplaySnapshot 中的有序展示 item。"""

    id: str
    event: str
    targetId: str
    status: str | None = None
    createdAt: str | None = None
    updatedAt: str = ""
    data: dict[str, Any] = Field(default_factory=dict)


class RunStateDisplayActor(BaseModel):
    """DisplaySnapshot v3 中的统一执行主体。"""

    id: str
    role: str = "single"
    agentId: str = ""
    agentName: str = ""
    activityId: str | None = None
    status: str = "running"
    task: str = ""
    assignment: str = ""
    expectedOutput: str = ""
    constraints: str = ""
    detail: str = ""
    output: str = ""
    report: str = ""
    skippedReason: str = ""
    reportReviewStatus: str = ""
    reportReviewNotes: str = ""
    reportReviewGaps: list[str] = Field(default_factory=list)
    executionMode: str = ""
    dependsOn: list[str] = Field(default_factory=list)
    waitReason: str = ""
    dependencyOutputCount: int = 0
    leaderContextCount: int = 0
    createdAt: str = ""
    updatedAt: str = ""
    completedAt: str = ""
    items: list[RunStateDisplayItem] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)


class RunStateDisplayBlock(BaseModel):
    """DisplaySnapshot 中的展示 block。"""

    id: str
    block: DisplayBlockKind
    status: str
    updatedAt: str = ""
    items: list[RunStateDisplayItem] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)


class RunStateDisplaySnapshot(BaseModel):
    """Chat 主展示完整快照。"""

    version: int = 3
    mode: DisplayMode | str = "single_agent"
    actors: list[RunStateDisplayActor] = Field(default_factory=list)
    blocks: list[RunStateDisplayBlock] = Field(default_factory=list)


class RunStateDisplayEvent(BaseModel):
    """单 Agent Chat 主展示增量事件。

    一个 RUN_STATE_PATCH 只携带一个 DisplayEvent，前端通过 reducer 聚合到
    DisplaySnapshot。
    """

    id: str
    runId: str
    sessionId: str
    seq: int = 0
    createdAt: str = ""
    scope: DisplayScope = "single_agent"
    block: DisplayBlockKind
    event: str
    op: DisplayEventOp
    targetId: str | None = None
    actorId: str | None = None
    status: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class RunStateSnapshot(BaseModel):
    """RunState v2 完整快照。"""

    version: int = 2
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
    skillCalls: list[dict[str, Any]] = Field(default_factory=list)
    modelName: str = ""
    tokenUsage: dict[str, Any] | None = None
    plans: list[RunStatePlanSummary] = Field(default_factory=list)
    displaySnapshot: RunStateDisplaySnapshot = Field(default_factory=RunStateDisplaySnapshot)
    approvals: list[RunStateApprovalSummary] = Field(default_factory=list)
    error: str = ""
    lastEventType: str = ""
    eventCount: int = 0


class RunStatePatch(BaseModel):
    """RunState v2 增量 patch。"""

    kind: RunStatePatchKind
    runId: str
    sessionId: str
    status: RunStateStatus | str
    currentStage: str = ""
    updatedAt: str = ""
    completedAt: str | None = None
    lastEventType: str = ""
    eventCount: int = 0
    modelName: str | None = None
    tokenUsage: dict[str, Any] | None = None
    resolvedApprovalId: str | None = None
    notice: dict[str, Any] | None = None
    displayEvent: RunStateDisplayEvent | None = None
    error: str | None = None


def dump_run_state(state: dict[str, Any]) -> dict[str, Any]:
    """校验并输出 RunState 快照。"""

    return RunStateSnapshot.model_validate(state).model_dump(mode="json")


def dump_run_state_patch(patch: dict[str, Any]) -> dict[str, Any]:
    """校验并输出 RunState patch。"""

    return RunStatePatch.model_validate(patch).model_dump(mode="json", exclude_none=True)
