"""Session 檔案與 Artifact。Run 完成不會自動把 Artifact 標成已驗收。"""

from __future__ import annotations

import hashlib
import mimetypes
import re
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile

from src.config.runtime_paths import session_artifacts_root, session_uploads_root
from src.sessions.errors import SessionError


def save_session_upload(session_id: str, filename: str, content: bytes) -> Path:
    """上傳落在 Session root，不寫進外部工作目錄。"""

    safe = Path(filename).name
    if not safe or safe in {".", ".."}:
        raise SessionError("invalid_filename", "檔名不合法")
    directory = session_uploads_root(session_id)
    directory.mkdir(parents=True, exist_ok=True)
    target = (directory / safe).resolve()
    if not target.is_relative_to(directory.resolve()):
        raise SessionError("path_escape", "上傳路徑越界")
    target.write_bytes(content)
    return target


def record_artifact(
    *,
    session_id: str,
    run_id: str,
    path: Path,
    kind: str = "managed",
) -> dict[str, str]:
    """登記 Artifact。驗收狀態與 Run 狀態分開，不同 Session 不共用同一筆。"""

    from src.storage import database

    database.ensure_db()
    digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""
    identity = hashlib.sha256(f"{session_id}\n{kind}\n{path}".encode()).hexdigest()
    artifact_id = "art_" + identity[:24]
    now = database._utc_now()
    with database._lock, database._connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO session_artifacts (
                id, session_id, owner_session_id, run_id, kind, path, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (artifact_id, session_id, session_id, run_id, kind, str(path), now),
        )
        conn.commit()
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return {
        "id": artifact_id,
        "sessionId": session_id,
        "runId": run_id,
        "sha256": digest,
        "mime": mime,
        "kind": kind,
        "accepted": "false",
        "path": str(path),
    }


def list_session_artifacts(session_id: str) -> list[dict[str, str]]:
    """只返回這個 Session 登記過的 Artifact。"""

    from src.storage import database

    database.ensure_db()
    with database._lock, database._connect() as conn:
        rows = conn.execute(
            """
            SELECT id, session_id, run_id, kind, path
            FROM session_artifacts
            WHERE session_id = ?
            ORDER BY created_at ASC
            """,
            (session_id,),
        ).fetchall()
    return [
        {
            "id": str(row["id"]),
            "sessionId": str(row["session_id"]),
            "runId": str(row["run_id"]),
            "kind": str(row["kind"]),
            "path": str(row["path"]),
        }
        for row in rows
    ]


def catalog_output(
    *,
    session_id: str,
    run_id: str,
    path: Path,
    kind: str,
) -> dict[str, str] | None:
    """把這次 Run 實際產出的檔案寫進目錄。失敗不影響呼叫方。"""

    try:
        if not path.is_file() or path.is_symlink():
            return None
        return record_artifact(session_id=session_id, run_id=run_id, path=path, kind=kind)
    except OSError:
        return None


UPLOAD_DIRECTORY_NAME = ".agent-workstation/uploads"
SESSION_DIRECTORY_NAME = ".agent-workstation/sessions"
JOB_DIRECTORY_NAME = ".agent-workstation/jobs"
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
_INVALID_FILENAME_CHARS = re.compile(r"[\x00/\\]")
_SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_JOB_PATH_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_INTERNAL_ARTIFACT_NAME = re.compile(
    r"^(tool_result([._-].*)?\.txt|\.ds_store|thumbs\.db|cachedir\.tag|pyvenv\.cfg)$",
    re.IGNORECASE,
)
_SCRATCH_DIR_NAMES = frozenset({
    "render", "tmp", "temp", "scratch", "_scratch", ".cache", "__pycache__",
    "subagents", "team", ".venv", "venv", "node_modules", "site-packages", ".git",
})
_OFFICE_SUFFIXES = frozenset({".docx", ".xlsx", ".pptx"})
_VALIDATION_SUFFIXES = frozenset({".pdf", ".png", ".jpg", ".jpeg", ".webp"})
_INTERMEDIATE_NAMES = frozenset({"source.md"})


def _validated_session_id(session_id: str) -> str:
    """限制 Session ID 用于目录名，避免上传路径被构造。"""

    value = str(session_id or "").strip()
    if not _SESSION_ID_PATTERN.fullmatch(value):
        raise HTTPException(status_code=422, detail="无效的 Session ID")
    return value


def project_upload_directory(project_root: Path, session_id: str) -> Path:
    """返回且创建当前 Session 的上傳目錄。位置在 Session root，不使用 project_root。"""

    from src.config.runtime_paths import session_uploads_root
    from src.config.runtime_paths import SessionPathError

    _ = project_root
    _validated_session_id(session_id)
    try:
        directory = session_uploads_root(session_id)
    except SessionPathError as exc:
        raise ValueError("附件目录超出了 Session root") from exc
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def project_session_directory(project_root: Path, session_id: str) -> Path:
    """Session 產物目錄在 Session root，不在外部工作目錄建立隱藏狀態。"""

    from src.config.runtime_paths import session_root

    del project_root
    directory = session_root(_validated_session_id(session_id))
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _validated_job_path_id(value: str, *, field_name: str) -> str:
    """限制 Job / firing ID 用于目录名；领域路径抛 ValueError，避免泄漏 HTTPException。"""

    text = str(value or "").strip()
    if not _JOB_PATH_ID_PATTERN.fullmatch(text):
        raise ValueError(f"无效的 {field_name}")
    return text


def project_job_run_directory(project_root: Path, job_id: str, firing_id: str) -> Path:
    """Job firing 產物留在 tenant root，不寫入外部 `.agent-workstation`。"""

    from src.config.runtime_paths import tenant_root

    del project_root
    root = tenant_root().resolve()
    safe_job = _validated_job_path_id(job_id, field_name="Job ID")
    safe_firing = _validated_job_path_id(firing_id, field_name="firing ID")
    directory = (root / "jobs" / safe_job / "runs" / safe_firing).resolve()
    if not directory.is_relative_to(root):
        raise ValueError("Job 目录超出了 tenant root")
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def list_project_session_artifacts(
    *,
    project_root: Path,
    session_id: str,
    session_workspace_dir: str = "",
) -> list[dict[str, object]]:
    """列出 Session artifacts 目錄中的可交付檔。共享外部目錄不在這份清單裡。"""

    from src.config.runtime_paths import SessionPathError, session_artifacts_root, session_root

    _ = project_root
    safe_session_id = _validated_session_id(session_id)
    try:
        directory = session_artifacts_root(safe_session_id)
        owner = session_root(safe_session_id).resolve()
    except SessionPathError:
        return []
    if session_workspace_dir:
        try:
            override = Path(session_workspace_dir).expanduser().resolve()
        except OSError:
            return []
        if not override.is_relative_to(owner):
            return []
        directory = override
    if not directory.is_dir():
        return []

    artifacts: list[dict[str, object]] = []
    for candidate in sorted(directory.rglob("*")):
        if not candidate.is_file() or candidate.is_symlink():
            continue
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if not resolved.is_relative_to(directory.resolve()) or not resolved.is_relative_to(owner):
            continue
        if not is_user_facing_session_artifact(resolved):
            continue
        artifacts_root = session_artifacts_root(safe_session_id).resolve()
        if not resolved.is_relative_to(artifacts_root):
            continue
        relative_path = "artifacts/" + resolved.relative_to(artifacts_root).as_posix()
        artifacts.append({
            "path": relative_path,
            "name": resolved.name,
            "extension": resolved.suffix.lstrip(".").upper() or "FILE",
            "size": resolved.stat().st_size,
        })
    return artifacts


def is_user_facing_session_artifact(path: Path | str) -> bool:
    """判断文件是否应出现在 Chat 前台下载列表。

    Session 目录里还有工具结果卸载、Word 校验渲染和中间稿；这些对用户没有交付意义。
    """

    candidate = Path(str(path or "").strip())
    name = candidate.name
    if not name or name.startswith("."):
        return False
    if _INTERNAL_ARTIFACT_NAME.match(name):
        return False
    if any(part.lower() in _SCRATCH_DIR_NAMES for part in candidate.parts[:-1]):
        return False
    suffix = candidate.suffix.lower()
    parent = candidate.parent
    siblings: list[Path] = []
    if parent.is_dir():
        try:
            siblings = [item for item in parent.iterdir() if item.is_file() and not item.is_symlink()]
        except OSError:
            siblings = []
    office_stems = {item.stem for item in siblings if item.suffix.lower() in _OFFICE_SUFFIXES}
    if name.lower() in _INTERMEDIATE_NAMES:
        return False
    if suffix in _VALIDATION_SUFFIXES and candidate.stem in office_stems:
        return False
    return True


def resolve_project_session_artifact(
    *,
    project_root: Path,
    session_id: str,
    relative_path: str,
) -> Path:
    """解析可下載產物。舊相對路徑、Windows 分隔符都落到 Session root。"""

    from src.sessions.errors import SessionError
    from src.sessions.file_paths import resolve_managed_file

    _ = project_root
    safe_session_id = _validated_session_id(session_id)
    try:
        return resolve_managed_file(safe_session_id, relative_path)
    except SessionError as exc:
        if exc.code == "not_found":
            raise HTTPException(status_code=404, detail="产物文件不存在") from exc
        if exc.code in {"external_denied", "symlink_escape"}:
            raise HTTPException(status_code=403, detail="产物文件不属于当前会话") from exc
        raise HTTPException(status_code=422, detail="无效的产物文件路径") from exc


def delete_project_session_files(*, project_root: Path, session_id: str) -> int:
    """刪除 Session 受管上傳與產物，不刪外部 working directory。"""

    from src.config.runtime_paths import SessionPathError
    from src.sessions.file_paths import delete_managed_files

    _ = project_root
    safe_session_id = _validated_session_id(session_id)
    try:
        return delete_managed_files(safe_session_id)
    except SessionPathError as exc:
        raise ValueError("Session 文件目录超出了 Session root") from exc


def _is_session_artifact_path(path: Path, session_id: str) -> bool:
    """判断路径是否位于 .agent-workstation/sessions/<sessionId> 下。"""

    for parent in path.parents:
        if (
            parent.name == session_id
            and parent.parent.name == "sessions"
            and parent.parent.parent.name == ".agent-workstation"
        ):
            return True
    return False


def _is_session_artifact_directory(directory: Path, session_id: str) -> bool:
    """判断目录是否为指定 Session 的产物根目录。"""

    return (
        directory.name == session_id
        and directory.parent.name == "sessions"
        and directory.parent.parent.name == ".agent-workstation"
    )


def sanitize_upload_filename(filename: str | None) -> str:
    """移除路径成分和不可见字符，保留用户可识别的文件名。"""

    value = _INVALID_FILENAME_CHARS.sub("_", str(filename or "").strip())
    value = value.replace("..", "_").strip(". ")
    return value[:180] or "attachment"


def _over_tenant_upload(size: int) -> bool:
    from src.config.settings import settings
    from src.tenancy.context import is_hosted_mode

    if not is_hosted_mode():
        return False
    return size > int(settings.tenant_upload_mb) * 1024 * 1024


async def save_project_upload(
    *,
    project_root: Path,
    session_id: str,
    upload: UploadFile,
) -> dict[str, object]:
    """分块写入 Session 附件，并返回 Project 相对路径元数据。"""

    original_name = sanitize_upload_filename(upload.filename)
    directory = project_upload_directory(project_root, session_id)
    stored_name = f"{uuid.uuid4().hex[:12]}__{original_name}"
    destination = (directory / stored_name).resolve()
    if not destination.is_relative_to(directory.resolve()):
        raise HTTPException(status_code=422, detail="无效的附件文件名")

    size = 0
    try:
        with destination.open("xb") as output:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES or _over_tenant_upload(size):
                    output.close()
                    destination.unlink(missing_ok=True)
                    if _over_tenant_upload(size):
                        from src.tenancy.quota import QuotaExceeded, assert_upload_size

                        try:
                            assert_upload_size(size)
                        except QuotaExceeded as exc:
                            raise exc.as_http() from None
                    raise HTTPException(status_code=413, detail="单个附件不能超过 50 MB")
                output.write(chunk)
    except HTTPException:
        raise
    except OSError as exc:
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="附件保存失败") from exc
    finally:
        await upload.close()

    return {
        "name": original_name,
        "path": f"uploads/{stored_name}",
        "size": size,
        "mediaType": str(upload.content_type or "application/octet-stream"),
    }


def validate_session_uploads(*, project_root: Path, session_id: str, paths: list[str]) -> None:
    """确认本轮引用的附件均是当前 Session 已上传的普通文件。"""

    from src.sessions.errors import SessionError
    from src.sessions.file_paths import resolve_managed_file

    if not paths:
        return
    _validated_session_id(session_id)
    _ = project_root
    for raw_path in paths:
        try:
            resolved = resolve_managed_file(session_id, str(raw_path or ""))
        except SessionError as exc:
            raise ValueError("附件不存在，或不属于当前 Session") from exc
        if "uploads" not in resolved.parts or not resolved.is_file():
            raise ValueError("附件不存在，或不属于当前 Session")
