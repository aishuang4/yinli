"""SessionError 到 HTTP 的唯一映射。"""

from __future__ import annotations

from fastapi import HTTPException

from src.sessions.errors import SessionError

_STATUS = {
    "session_not_found": 404,
    "session_archived": 409,
    "directory_unavailable": 422,
    "session_not_migrated": 409,
    "resource_not_found": 409,
    "resource_disabled": 409,
    "agent_ineligible": 409,
    "tenant_mismatch": 409,
    "session_has_active_runs": 409,
    "hosted_path_rejected": 409,
    "snapshot_required": 500,
    "legacy_scope_retired": 410,
}


def session_error_http(exc: SessionError, *, default_status: int) -> HTTPException:
    """表內錯誤碼使用固定狀態，其餘沿用呼叫方預設。"""

    return HTTPException(
        status_code=_STATUS.get(exc.code, default_status),
        detail={"code": exc.code, "message": exc.message},
    )
