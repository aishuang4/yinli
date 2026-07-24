"""Plan Mode 状态模型。"""

from dataclasses import dataclass, field


PLAN_STATUS_DRAFTING = "drafting"
PLAN_STATUS_REVIEW_WAITING = "review_waiting"
PLAN_STATUS_REVISING = "revising"
PLAN_STATUS_APPROVED = "approved"
PLAN_STATUS_EXECUTING = "executing"
PLAN_STATUS_REPLANNING = "replanning"
PLAN_STATUS_COMPLETED = "completed"
PLAN_STATUS_FAILED = "failed"
PLAN_STATUS_CANCELLED = "cancelled"
PLAN_ACTIVE_STATUSES = (
    PLAN_STATUS_DRAFTING,
    PLAN_STATUS_REVIEW_WAITING,
    PLAN_STATUS_REVISING,
    PLAN_STATUS_APPROVED,
    PLAN_STATUS_EXECUTING,
    PLAN_STATUS_REPLANNING,
)

PLAN_STEP_STATUS_PENDING = "pending"
PLAN_STEP_STATUS_RUNNING = "running"
PLAN_STEP_STATUS_COMPLETED = "completed"
PLAN_STEP_STATUS_FAILED = "failed"
PLAN_STEP_STATUS_SKIPPED = "skipped"
PLAN_STEP_STATUSES = frozenset({
    PLAN_STEP_STATUS_PENDING,
    PLAN_STEP_STATUS_RUNNING,
    PLAN_STEP_STATUS_COMPLETED,
    PLAN_STEP_STATUS_FAILED,
    PLAN_STEP_STATUS_SKIPPED,
})


@dataclass
class PlanModeContext:
    """一个可跨 Chat run 延续的 Plan 会话状态。"""

    session_id: str
    run_id: str
    agent_id: str
    agent_name: str
    plan_id: str = ""
    plan_active: bool = False
    current_plan_file: str = ""
    markdown: str = ""
    steps: list[dict] = field(default_factory=list)
    title: str = ""
    goal: str = ""
    status: str = ""
    revision: int = 0
    approved_revision: int | None = None
    feedback: str = ""
    execution_run_id: str = ""
