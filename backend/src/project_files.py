"""Project 范围内用户附件的受控存储。"""

from __future__ import annotations

import re
import shutil
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile


UPLOAD_DIRECTORY_NAME = ".agent-workstation/uploads"
SESSION_DIRECTORY_NAME = ".agent-workstation/sessions"
JOB_DIRECTORY_NAME = ".agent-workstation/jobs"
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
_INVALID_FILENAME_CHARS = re.compile(r"[\x00/\\]")
_SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_JOB_PATH_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_INTERNAL_ARTIFACT_NAME = re.compile(
    r"^(tool_result([._-].*)?\.txt|\.ds_store|thumbs\.db)$",
    re.IGNORECASE,
)
_SCRATCH_DIR_NAMES = frozenset({
    "render", "tmp", "temp", "_scratch", ".cache", "__pycache__", "subagents", "team",
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
    """返回且创建当前 Session 专属的 Project 附件目录。"""

    directory = (project_root / UPLOAD_DIRECTORY_NAME / _validated_session_id(session_id)).resolve()
    if not directory.is_relative_to(project_root.resolve()):
        raise ValueError("附件目录超出了 Project root")
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def project_session_directory(project_root: Path, session_id: str) -> Path:
    """返回且创建 Session 专属的 Agent 产物工作目录。"""

    directory = (project_root / SESSION_DIRECTORY_NAME / _validated_session_id(session_id)).resolve()
    if not directory.is_relative_to(project_root.resolve()):
        raise ValueError("Session 目录超出了 Project root")
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _validated_job_path_id(value: str, *, field_name: str) -> str:
    """限制 Job / firing ID 用于目录名；领域路径抛 ValueError，避免泄漏 HTTPException。"""

    text = str(value or "").strip()
    if not _JOB_PATH_ID_PATTERN.fullmatch(text):
        raise ValueError(f"无效的 {field_name}")
    return text


def project_job_run_directory(project_root: Path, job_id: str, firing_id: str) -> Path:
    """返回且创建一次 Job firing 的产物工作目录。"""

    root = project_root.resolve()
    safe_job = _validated_job_path_id(job_id, field_name="Job ID")
    safe_firing = _validated_job_path_id(firing_id, field_name="firing ID")
    directory = (root / JOB_DIRECTORY_NAME / safe_job / "runs" / safe_firing).resolve()
    if not directory.is_relative_to(root):
        raise ValueError("Job firing 目录超出了 Project root")
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def list_project_session_artifacts(
    *,
    project_root: Path,
    session_id: str,
    session_workspace_dir: str = "",
) -> list[dict[str, object]]:
    """列出当前 Session 产物目录中真实存在的普通文件。"""

    root = project_root.resolve()
    safe_session_id = _validated_session_id(session_id)
    directory = (
        Path(session_workspace_dir).expanduser().resolve()
        if session_workspace_dir
        else root / SESSION_DIRECTORY_NAME / safe_session_id
    )
    if (
        not directory.is_dir()
        or not directory.is_relative_to(root)
        or not _is_session_artifact_directory(directory, safe_session_id)
    ):
        return []

    artifacts: list[dict[str, object]] = []
    for candidate in sorted(directory.rglob("*")):
        if not candidate.is_file() or candidate.is_symlink():
            continue
        resolved = candidate.resolve()
        if not resolved.is_relative_to(directory) or not resolved.is_relative_to(root):
            continue
        if not is_user_facing_session_artifact(resolved):
            continue
        relative_path = str(resolved.relative_to(root))
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
    """解析可下载产物，并限制其必须位于指定 Session 产物目录。"""

    root = project_root.resolve()
    safe_session_id = _validated_session_id(session_id)
    path = Path(str(relative_path or "").strip())
    if not path.parts or path.is_absolute() or ".." in path.parts:
        raise HTTPException(status_code=422, detail="无效的产物文件路径")
    candidate = (root / path).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        raise HTTPException(status_code=404, detail="产物文件不存在")
    if not _is_session_artifact_path(candidate, safe_session_id):
        raise HTTPException(status_code=403, detail="产物文件不属于当前会话")
    return candidate


def delete_project_session_files(*, project_root: Path, session_id: str) -> int:
    """删除当前 Session 的用户附件和 Agent 产物，返回已移除目录数。"""

    root = project_root.resolve()
    safe_session_id = _validated_session_id(session_id)
    directories = (
        (root / UPLOAD_DIRECTORY_NAME, safe_session_id),
        (root / SESSION_DIRECTORY_NAME, safe_session_id),
    )
    removed = 0
    for base_directory, name in directories:
        target = base_directory / name
        if target.parent != base_directory or not target.is_relative_to(root):
            raise ValueError("Session 文件目录超出了 Project root")
        if not target.exists() and not target.is_symlink():
            continue
        if target.is_symlink() or target.is_file():
            target.unlink()
        else:
            shutil.rmtree(target)
        removed += 1
    return removed


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
    if not destination.is_relative_to(directory):
        raise HTTPException(status_code=422, detail="无效的附件文件名")

    size = 0
    try:
        with destination.open("xb") as output:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    output.close()
                    destination.unlink(missing_ok=True)
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
        "path": str(destination.relative_to(project_root.resolve())),
        "size": size,
        "mediaType": str(upload.content_type or "application/octet-stream"),
    }


def validate_session_uploads(*, project_root: Path, session_id: str, paths: list[str]) -> None:
    """确认本轮引用的附件均是当前 Session 已上传的普通文件。"""

    if not paths:
        return
    directory = project_upload_directory(project_root, session_id)
    for raw_path in paths:
        candidate = (project_root / str(raw_path or "")).resolve()
        if not candidate.is_relative_to(directory) or not candidate.is_file():
            raise ValueError("附件不存在，或不属于当前 Session")
