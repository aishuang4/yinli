"""工作室共享文件库：上传写入 owner Session 的 `group-studio/{id}/inputs/`。

不在外部工作目录建立 `.agent-workstation/group-studios`。
"""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import UploadFile

from src.config.runtime_paths import SessionPathError, session_group_studio_root, session_root
from src.group_studio.errors import INVALID_UPLOAD, GroupStudioError
from src.sessions.files import sanitize_upload_filename

MAX_STUDIO_UPLOAD_BYTES = 50 * 1024 * 1024


def studio_workspace(session_id: str, group_id: str) -> Path:
    """工作室根目录：`<session-root>/group-studio/<group>`。"""

    try:
        return session_group_studio_root(session_id, group_id)
    except SessionPathError as exc:
        raise GroupStudioError(422, INVALID_UPLOAD) from exc


def ensure_studio_dirs(session_id: str, group_id: str) -> Path:
    """确保 inputs / workspace / outputs 存在。"""

    root = studio_workspace(session_id, group_id)
    for name in ("inputs", "workspace", "outputs"):
        (root / name).mkdir(parents=True, exist_ok=True)
    return root


def _reject_illegal_filename(filename: str | None) -> str:
    """拒绝路径穿越和被消毒改写的文件名。"""

    raw = str(filename or "").strip()
    if not raw or "/" in raw or "\\" in raw or ".." in raw:
        raise GroupStudioError(422, INVALID_UPLOAD)
    cleaned = sanitize_upload_filename(raw)
    if cleaned != raw:
        raise GroupStudioError(422, INVALID_UPLOAD)
    return cleaned


async def save_studio_upload(
    *,
    session_id: str,
    group_id: str,
    upload: UploadFile,
) -> dict[str, object]:
    """把 multipart 文件写入工作室 inputs，路径相对 Session root。"""

    original_name = _reject_illegal_filename(upload.filename)
    root = session_root(session_id).resolve()
    inputs = (ensure_studio_dirs(session_id, group_id) / "inputs").resolve()
    if not inputs.is_relative_to(root):
        raise GroupStudioError(422, INVALID_UPLOAD)
    stored_name = f"{uuid.uuid4().hex[:12]}__{original_name}"
    destination = (inputs / stored_name).resolve()
    if not destination.is_relative_to(inputs):
        raise GroupStudioError(422, INVALID_UPLOAD)

    size = 0
    try:
        with destination.open("xb") as output:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_STUDIO_UPLOAD_BYTES:
                    output.close()
                    destination.unlink(missing_ok=True)
                    raise GroupStudioError(422, INVALID_UPLOAD)
                output.write(chunk)
    except GroupStudioError:
        raise
    except OSError as exc:
        destination.unlink(missing_ok=True)
        raise GroupStudioError(422, INVALID_UPLOAD) from exc
    finally:
        await upload.close()

    return {
        "title": original_name,
        "path": destination.relative_to(root).as_posix(),
        "byteSize": size,
        "mediaType": str(upload.content_type or "application/octet-stream"),
    }
