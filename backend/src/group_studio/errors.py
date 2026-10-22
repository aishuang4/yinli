"""群聊工作室稳定错误码。HTTP `detail` 使用这些字符串，便于前端匹配。"""

from fastapi import HTTPException

GROUP_STUDIO_DISABLED = "group_studio_disabled"
CHAT_RUN_ACTIVE = "chat_run_active"
MEMBER_CAP = "member_cap"
AGENT_NOT_BOUND = "agent_not_bound"
JOB_AGENT_FORBIDDEN = "job_agent_forbidden"
DEFAULT_AGENT_FORBIDDEN = "default_agent_forbidden"
HOST_REQUIRED = "host_required"
STUDIO_PAUSED = "studio_paused"
STUDIO_CIRCUIT_BROKEN = "studio_circuit_broken"
ILLEGAL_TRANSITION = "illegal_transition"
FLOOR_NOT_GRANTED = "floor_not_granted"
APPROVAL_NOT_PENDING = "approval_not_pending"
CROSS_PROJECT_SESSION = "cross_project_session"
WORKER_SESSION_LOST = "worker_session_lost"
INVALID_UPLOAD = "invalid_upload"
MODEL_NOT_CONFIGURED = "model_not_configured"


GROUP_STUDIO_NOT_FOUND = "not_found"
STUDIO_NOT_OPEN = "studio_not_open"
STUDIO_BUSY = "studio_busy"
NO_LAST_TURN = "no_last_turn"
NO_ELIGIBLE_SPEAKER = "no_eligible_speaker"
ALREADY_MEMBER = "already_member"


class GroupStudioError(Exception):
    """领域错误，由路由层映射为稳定 HTTP code。"""

    def __init__(self, status_code: int, code: str) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code


def group_studio_http_error(status_code: int, code: str) -> HTTPException:
    """构造 detail 为稳定 code 的 HTTPException。"""

    return HTTPException(status_code=status_code, detail=code)
