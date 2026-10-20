"""Job firing 工作目錄只在 owner Session 內，並受 allowed roots 約束。"""

from __future__ import annotations

from pathlib import Path

from src.config.runtime_paths import SessionPathError, session_job_firing_root, session_root


def resolve_job_workspace(session_id: str, job_id: str, firing_id: str) -> Path:
    """建立 `<session-root>/jobs/<job>/firings/<firing>`。只讀授權不能當寫入 cwd。"""

    try:
        directory = session_job_firing_root(session_id, job_id, firing_id)
    except SessionPathError as exc:
        raise ValueError(f"Job firing 路徑不合法: {exc}") from exc
    _assert_writable(session_id, directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "outputs").mkdir(exist_ok=True)
    return directory


def _assert_writable(session_id: str, directory: Path) -> None:
    root = session_root(session_id).resolve()
    resolved = directory.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("Job firing 目錄超出 Session")
    from src.storage import database

    database.ensure_db()
    with database._lock, database._connect() as conn:
        rows = conn.execute(
            """
            SELECT normalized_path, access_mode FROM session_allowed_roots
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchall()
    for row in rows:
        granted = Path(str(row["normalized_path"])).expanduser()
        try:
            granted = granted.resolve()
        except OSError as exc:
            raise ValueError("allowed root 無法解析") from exc
        if resolved.is_relative_to(granted) and str(row["access_mode"]) == "read":
            raise ValueError("Job firing 落在只讀 allowed root")
