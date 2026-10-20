"""全局 Session 遷移的只讀狀態 API。"""

from fastapi import APIRouter

from src.session_migration.state import cutover_state

router = APIRouter()


@router.get("/migrations/global-session-v1")
def get_global_session_migration_state() -> dict:
    """回傳 flag 與 cutover 階段。不建立遷移目錄，也不開始回填。"""

    state = cutover_state()
    state["cutoverState"] = state["cutover"]
    return state
