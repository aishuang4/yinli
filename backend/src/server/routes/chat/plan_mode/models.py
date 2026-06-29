"""Plan Mode 状态模型。"""

from dataclasses import dataclass


PLAN_STATUS_DRAFT = "draft"
PLAN_STATUS_WAITING_APPROVAL = "waiting_approval"
PLAN_STATUS_APPROVED = "approved"
PLAN_STATUS_DENIED = "denied"


@dataclass
class PlanModeContext:
    """单次 Chat run 内的 Plan Mode 状态。"""

    session_id: str
    run_id: str
    agent_id: str
    agent_name: str
    plan_id: str = ""
    plan_active: bool = False
    waiting_approval: bool = False
    approved: bool = False
    current_plan_file: str = ""
    markdown: str = ""
