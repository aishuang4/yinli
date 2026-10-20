"""Session 目錄授權更新、hosted 枚舉與內部 Artifact 工作區。"""

from __future__ import annotations

from pathlib import Path

from src.config.runtime_paths import session_artifacts_root, tenants_root
from src.sessions.errors import SessionError
from src.sessions.records import add_allowed_root, get_run_snapshot, get_session, list_allowed_roots
from src.sessions.service import deletion_blockers
from src.tenancy.context import current_tenant


def session_work_directory(session_id: str) -> Path:
    """沒有外部 working directory 時，讀寫落在 Session artifacts。"""

    session = get_session(session_id)
    if session is None:
        raise SessionError("session_not_found", "Session 不存在")
    external = str(session["workingDirectory"] or "").strip()
    if external:
        path = Path(external).expanduser().resolve()
        if not path.is_dir():
            raise SessionError("directory_unavailable", "外部工作目錄不可用")
        return path
    root = session_artifacts_root(session_id)
    root.mkdir(parents=True, exist_ok=True)
    return root


def hosted_directory_choices() -> list[str]:
    """hosted 只能枚舉目前 tenant 內已存在的目錄。"""

    tenant = current_tenant()
    if not tenant.is_hosted:
        return []
    root = tenant.data_root.resolve()
    choices: list[str] = []
    if root.is_dir():
        choices.append(str(root))
        for child in sorted(root.iterdir()):
            if child.is_dir() and not child.is_symlink():
                choices.append(str(child.resolve()))
    return choices


def _reject_unlisted_hosted_path(path: Path) -> None:
    tenant = current_tenant()
    if not tenant.is_hosted:
        return
    home = Path.home().resolve()
    tenant_root = tenant.data_root.resolve()
    if path == home or (path.is_relative_to(home) and not path.is_relative_to(tenant_root)):
        raise SessionError("hosted_path_rejected", "不能授權宿主 HOME")
    tenants = tenants_root().resolve()
    if path.is_relative_to(tenants) and not path.is_relative_to(tenant.data_root.resolve()):
        raise SessionError("tenant_mismatch", "不能授權其他 tenant 的目錄")
    allowed = {Path(item).resolve() for item in hosted_directory_choices()}
    if path.resolve() not in allowed and not any(path.resolve().is_relative_to(item) for item in allowed):
        raise SessionError("hosted_path_rejected", "路徑不在服務端允許枚舉中")


def replace_directory_grant(
    session_id: str,
    path: str,
    *,
    access_mode: str = "read_write",
    source: str = "user",
) -> dict[str, object]:
    """更新目錄授權。進行中的 Run 不能改 scope，也不會改寫既有 snapshot。"""

    if get_session(session_id) is None:
        raise SessionError("session_not_found", "Session 不存在")
    blockers = deletion_blockers(session_id)
    if blockers["runs"] or blockers["hitl"]:
        raise SessionError("session_has_active_runs", "進行中的 Run 不能更改目錄授權", details=blockers)
    candidate = Path(path).expanduser()
    try:
        resolved = candidate.resolve()
    except OSError as exc:
        raise SessionError("directory_unavailable", "目錄無法解析") from exc
    if not resolved.is_dir():
        raise SessionError("directory_unavailable", "目錄不存在或不是目錄")
    _reject_unlisted_hosted_path(resolved)
    before = {
        item["runId"]: get_run_snapshot(item["runId"])
        for item in _snapshot_rows(session_id)
    }
    from src.storage import database

    database.ensure_db()
    with database._lock, database._connect() as conn:
        conn.execute("DELETE FROM session_allowed_roots WHERE session_id = ?", (session_id,))
        conn.execute(
            "UPDATE chat_sessions SET working_directory = ? WHERE id = ?",
            (str(resolved), session_id),
        )
        conn.commit()
    saved = add_allowed_root(session_id, str(resolved), access_mode=access_mode, source=source)
    for run_id, snapshot in before.items():
        if get_run_snapshot(run_id) != snapshot:
            raise SessionError("snapshot_mutated", "目錄更新改寫了既有 Run snapshot")
    return {
        "sessionId": session_id,
        "path": str(resolved),
        "accessMode": access_mode,
        "directoryGrantVersion": saved["directoryGrantVersion"],
        "roots": list_allowed_roots(session_id),
    }


def docker_mount_snapshot(session_id: str) -> list[dict[str, str]]:
    """Docker 掛載只來自已保存的 tenant 內授權，不接受客戶端臨時絕對路徑。"""

    tenant = current_tenant()
    mounts: list[dict[str, str]] = []
    for item in list_allowed_roots(session_id):
        path = Path(item["normalizedPath"]).expanduser().resolve()
        if tenant.is_hosted:
            _reject_unlisted_hosted_path(path)
        mounts.append({
            "source": str(path),
            "target": "/workspace",
            "mode": str(item["accessMode"]),
        })
    return mounts


def _snapshot_rows(session_id: str) -> list[dict[str, str]]:
    from src.storage import database

    database.ensure_db()
    with database._lock, database._connect() as conn:
        rows = conn.execute(
            "SELECT run_id FROM run_context_snapshots WHERE session_id = ?",
            (session_id,),
        ).fetchall()
    return [{"runId": str(row["run_id"])} for row in rows]
