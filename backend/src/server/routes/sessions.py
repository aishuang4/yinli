"""Session 目錄與 Artifact API。"""

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from src.config.runtime_paths import session_artifacts_root
from src.sessions.directories import hosted_directory_choices, replace_directory_grant
from src.sessions.errors import SessionError
from src.sessions.intake import ensure_session_for_message
from src.sessions.records import create_session
from src.session_migration.legacy_fields import LEGACY_SCOPE_FIELDS
from src.sessions.resources import get_session_resources, put_session_resources

router = APIRouter()


class SessionCreateBody(BaseModel):
    """先建立 Session，再發送第一則訊息。"""

    title: str = "新對話"
    agent_id: str = Field(default="default", alias="agentId")
    working_directory: str = Field(default="", alias="workingDirectory")

    model_config = {"populate_by_name": True, "extra": "allow"}


@router.post("/sessions", status_code=201)
def post_session(body: SessionCreateBody) -> dict:
    if LEGACY_SCOPE_FIELDS & set(body.model_extra or {}):
        raise HTTPException(
            status_code=410,
            detail={"code": "legacy_scope_retired", "message": "舊欄位不能更改 Session 歸屬或擴大目錄"},
        )
    return create_session(
        title=body.title,
        agent_id=body.agent_id,
        working_directory=body.working_directory,
    )


class DirectoryGrantBody(BaseModel):
    """使用者選擇的目錄。服務端再做 realpath。"""

    path: str
    access_mode: str = Field(default="read_write", alias="accessMode")
    source: str = "user"

    model_config = {"populate_by_name": True}


def _http_error(exc: SessionError) -> HTTPException:
    from src.server.routes.session_errors import session_error_http

    return session_error_http(exc, default_status=404)


@router.get("/sessions/directory-roots")
def list_directory_roots() -> dict:
    """目前 tenant 未歸檔 Session 的工作目錄與授權根。"""

    from pathlib import Path

    from src.sessions.records import list_allowed_roots
    from src.storage import database

    roots: set[str] = set()
    database.ensure_db()
    with database._lock, database._connect() as conn:
        rows = conn.execute(
            "SELECT id, working_directory, status FROM chat_sessions",
        ).fetchall()
    for row in rows:
        if str(row["status"] or "") == "archived":
            continue
        working = str(row["working_directory"] or "")
        if working:
            path = Path(working).expanduser()
            if path.is_dir():
                roots.add(str(path.resolve()))
        for item in list_allowed_roots(str(row["id"])):
            path = Path(str(item["normalizedPath"])).expanduser()
            if path.is_dir():
                roots.add(str(path.resolve()))
    return {"roots": sorted(roots)}


@router.get("/sessions/{session_id}")
def get_session_detail(session_id: str) -> dict:
    from src.sessions.records import get_session

    session = get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail={"code": "session_not_found", "message": "Session 不存在"})
    return session


@router.get("/sessions/{session_id}/directories/choices")
def list_directory_choices(session_id: str) -> dict:
    """hosted 返回服務端枚舉。桌面端由本機對話框選擇，這裡不接受客戶端絕對路徑。"""

    del session_id
    return {"paths": hosted_directory_choices()}


class ResourceGrantBody(BaseModel):
    """一批 Session 資源授權。"""

    resources: list[dict] = Field(default_factory=list)


@router.get("/sessions/{session_id}/resources")
def get_resources(session_id: str) -> dict:
    try:
        return get_session_resources(session_id)
    except SessionError as exc:
        raise _http_error(exc) from exc


@router.put("/sessions/{session_id}/resources")
def put_resources(session_id: str, body: ResourceGrantBody) -> dict:
    try:
        return put_session_resources(session_id, body.resources)
    except SessionError as exc:
        status = 422 if exc.code in {"resource_disabled", "agent_ineligible", "invalid_override", "invalid_resource_type"} else 404
        if exc.code == "cross_tenant_resource":
            status = 403
        raise HTTPException(status_code=status, detail={"code": exc.code, "message": exc.message}) from exc


@router.put("/sessions/{session_id}/directories")
def put_directory_grant(session_id: str, body: DirectoryGrantBody) -> dict:
    try:
        return replace_directory_grant(
            session_id,
            body.path,
            access_mode=body.access_mode,
            source=body.source,
        )
    except SessionError as exc:
        raise _http_error(exc) from exc


@router.post("/sessions/{session_id}/files", status_code=201)
async def upload_session_file(session_id: str, file: UploadFile = File(...)) -> dict:
    """附件寫入 Session root，不經過 Project。"""

    from pathlib import Path

    from src.sessions.files import save_project_upload
    from src.sessions.records import get_session

    if get_session(session_id) is None:
        raise HTTPException(status_code=404, detail={"code": "session_not_found", "message": "Session 不存在"})
    return await save_project_upload(project_root=Path("."), session_id=session_id, upload=file)


@router.get("/sessions/{session_id}/artifacts/{name}")
def download_session_artifact(session_id: str, name: str) -> FileResponse:
    """下載 Session artifacts 內的檔案，供預覽與下載。"""

    try:
        root = session_artifacts_root(session_id)
        root.mkdir(parents=True, exist_ok=True)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail={"code": "session_not_found", "message": str(exc)}) from exc
    target = (root / name).resolve()
    if not target.is_relative_to(root.resolve()) or not target.is_file():
        raise HTTPException(status_code=404, detail={"code": "artifact_not_found", "message": "找不到 Artifact"})
    return FileResponse(target)
