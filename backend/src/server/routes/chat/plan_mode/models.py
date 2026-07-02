"""Plan Mode 状态模型。"""

from dataclasses import dataclass, field


PLAN_STATUS_DRAFT = "draft"
PLAN_STATUS_FINALIZED = "finalized"

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
    """单次 Chat run 内的 Plan Mode 状态。"""

    session_id: str
    run_id: str
    agent_id: str
    agent_name: str
    plan_id: str = ""
    plan_active: bool = False
    current_plan_file: str = ""
    markdown: str = ""
    steps: list[dict] = field(default_factory=list)
