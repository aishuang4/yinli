"""Session 上傳、產物、預覽與刪除的路徑服務。

舊的 Project 相對路徑會對應到 Session root。外部 working directory 只作為引用，不在這裡刪除。
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

from src.config.runtime_paths import session_artifacts_root, session_uploads_root
from src.sessions.errors import SessionError

_DRIVE = re.compile(r"^[A-Za-z]:")


def normalize_relative(raw: str) -> str:
    """把使用者路徑收成相對 POSIX，拒絕絕對路徑、Windows 磁碟與 `..`。"""

    text = str(raw or "").strip().replace("\\", "/")
    if not text or "\x00" in text:
        raise SessionError("invalid_path", "路徑不合法")
    if text.startswith("/") or text.startswith("//") or _DRIVE.match(text):
        raise SessionError("absolute_path", "不能用絕對路徑讀取 Session 檔案")
    parts = [part for part in PurePosixPath(text).parts if part not in {"", "."}]
    if any(part == ".." for part in parts):
        raise SessionError("path_escape", "路徑越界")
    if not parts:
        raise SessionError("invalid_path", "路徑不合法")
    return "/".join(parts)


def split_session_path(session_id: str, raw: str) -> tuple[str, str]:
    """辨認 uploads / artifacts，並把舊 `.agent-workstation` 相對路徑對到同一棵樹。"""

    relative = normalize_relative(raw)
    legacy_upload = f".agent-workstation/uploads/{session_id}/"
    legacy_artifact = f".agent-workstation/sessions/{session_id}/"
    if relative.startswith("uploads/"):
        return "uploads", relative[len("uploads/"):]
    if relative.startswith("artifacts/"):
        return "artifacts", relative[len("artifacts/"):]
    if relative.startswith(legacy_upload):
        return "uploads", relative[len(legacy_upload):]
    if relative.startswith(legacy_artifact):
        return "artifacts", relative[len(legacy_artifact):]
    return "artifacts", relative


def _bucket_root(session_id: str, bucket: str) -> Path:
    if bucket == "uploads":
        return session_uploads_root(session_id)
    return session_artifacts_root(session_id)


def resolve_managed_file(session_id: str, raw: str) -> Path:
    """解析 Session 受管檔。軟連結或越出 bucket 的目標會被拒絕。"""

    bucket, remainder = split_session_path(session_id, raw)
    if not remainder or remainder.endswith("/"):
        raise SessionError("invalid_path", "路徑不合法")
    root = _bucket_root(session_id, bucket)
    candidate = root.joinpath(*remainder.split("/"))
    if candidate.is_symlink() or any(parent.is_symlink() for parent in candidate.parents if parent != candidate):
        raise SessionError("symlink_escape", "不能經由符號連結離開 Session")
    try:
        resolved = candidate.resolve()
        base = root.resolve()
    except OSError as exc:
        raise SessionError("invalid_path", "路徑不合法") from exc
    if not resolved.is_relative_to(base) or not resolved.is_file():
        raise SessionError("not_found", "檔案不存在")
    return resolved


def classify_reference(session_id: str, raw: str, *, working_directory: str = "") -> dict[str, str]:
    """受管路徑留在 Session root；已授權外部路徑只回傳引用。"""

    text = str(raw or "").strip()
    portable = text.replace("\\", "/")
    external = portable.startswith("/") or portable.startswith("//") or bool(_DRIVE.match(portable))
    if not external:
        path = resolve_managed_file(session_id, text)
        bucket, _remainder = split_session_path(session_id, text)
        relative = path.relative_to(_bucket_root(session_id, bucket).resolve()).as_posix()
        return {"kind": "managed", "path": str(path), "relative": f"{bucket}/{relative}"}
    if not working_directory:
        raise SessionError("external_denied", "外部路徑不在授權目錄")
    try:
        resolved = Path(text).expanduser().resolve()
        root = Path(working_directory).expanduser().resolve()
    except OSError as exc:
        raise SessionError("external_denied", "外部路徑不在授權目錄") from exc
    if resolved != root and not resolved.is_relative_to(root):
        raise SessionError("external_denied", "外部路徑不在授權目錄")
    if resolved.is_symlink():
        raise SessionError("symlink_escape", "不能經由符號連結離開授權目錄")
    return {"kind": "external", "path": str(resolved), "relative": str(resolved)}


def delete_managed_files(session_id: str) -> int:
    """只刪 Session root 裡的 uploads 與 artifacts，不碰到外部目錄。"""

    import shutil

    removed = 0
    for root in (session_uploads_root(session_id), session_artifacts_root(session_id)):
        if not root.exists() and not root.is_symlink():
            continue
        if root.is_symlink() or root.is_file():
            root.unlink()
        else:
            shutil.rmtree(root)
        removed += 1
    return removed
