"""群聊工作室稳定错误码。HTTP `detail` 使用这些字符串，便于前端匹配。"""

from fastapi import HTTPException

GROUP_STUDIO_DISABLED = "group_studio_disabled"
GROUP_STUDIO_NOT_IMPLEMENTED = "group_studio_not_implemented"
CHAT_RUN_ACTIVE = "chat_run_active"
MEMBER_CAP = "member_cap"
AGENT_NOT_BOUND = "agent_not_bound"
JOB_AGENT_FORBIDDEN = "job_agent_forbidden"
HOST_REQUIRED = "host_required"
STUDIO_PAUSED = "studio_paused"
STUDIO_CIRCUIT_BROKEN = "studio_circuit_broken"
NOT_AWAITING_RESTATEMENT = "not_awaiting_restatement"
RESTATEMENT_MISSING = "restatement_missing"
DOD_INCOMPLETE = "dod_incomplete"
ILLEGAL_TRANSITION = "illegal_transition"
DRAFTING_NO_FLOOR = "drafting_no_floor"
APPROVAL_NOT_PENDING = "approval_not_pending"
GOAL_REQUIRED = "goal_required"
CROSS_PROJECT_SESSION = "cross_project_session"
WORKER_SESSION_LOST = "worker_session_lost"
APPROVAL_BATCH_INCOMPLETE = "approval_batch_incomplete"


def group_studio_http_error(status_code: int, code: str) -> HTTPException:
    """构造 detail 为稳定 code 的 HTTPException。"""

    return HTTPException(status_code=status_code, detail=code)
