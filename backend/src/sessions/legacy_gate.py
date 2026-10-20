"""未遷移歷史 Session 的新 Run 准入。只讀舊欄位，不推測目錄或資源。"""

from __future__ import annotations

from src.sessions.errors import SessionError
from src.sessions.records import get_session, list_allowed_roots, list_resource_grants


def require_migrated_session(session_id: str) -> None:
    """五項同時成立時拒絕新 Run。只讀查詢不呼叫本函式。"""

    from src.session_migration.state import cutover_state

    session = get_session(session_id)
    if session is None:
        return
    project_id = str(session.get("projectId") or "")
    working = str(session.get("workingDirectory") or "")
    if (
        cutover_state()["cutover"] != "cutover"
        and project_id
        and not working
        and not list_allowed_roots(session_id)
        and not list_resource_grants(session_id)
    ):
        raise SessionError(
            "session_not_migrated",
            "该对话来自旧版本，需完成数据迁移后才能继续",
            details={"projectId": project_id},
        )
