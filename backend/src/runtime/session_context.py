"""不可變 SessionContext / GlobalContext。業務域只能經這兩個建構入口取得。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from src.config.runtime_paths import session_artifacts_root, session_root
from src.sessions.errors import SessionError
from src.sessions.records import get_session, list_allowed_roots
from src.tenancy.context import current_tenant


@dataclass(frozen=True)
class GlobalContext:
    """全局 catalog 維護上下文。不建立假 Session。"""

    tenant_id: str
    tenant_root: Path


@dataclass(frozen=True)
class SessionContext:
    """一次執行使用的 Session 歸屬。呼叫方請用 build_session_context。"""

    tenant_id: str
    session_id: str
    session_kind: str
    session_root: Path
    working_directory: Path | None
    allowed_roots: tuple[Path, ...]
    directory_grant_version: int
    resource_grant_version: int
    agent_id: str
    run_id: str
    run_snapshot_id: str

    @property
    def cwd(self) -> Path:
        """工作目錄有效時用它，否則用 Session 產物根。"""

        if self.working_directory is not None:
            return self.working_directory
        return session_artifacts_root(self.session_id)

    @property
    def project_root(self) -> Path:
        """檔案範圍用的目錄。與 cwd 相同。"""

        return self.cwd

    def with_run_id(self, run_id: str) -> "SessionContext":
        return replace(self, run_id=run_id)


def build_global_context() -> GlobalContext:
    """目前租戶的全局上下文。"""

    tenant = current_tenant()
    return GlobalContext(tenant_id=tenant.tenant_id, tenant_root=tenant.data_root)


def build_session_context(
    session_id: str,
    *,
    agent_id: str = "",
    run_id: str = "",
    run_snapshot_id: str = "",
) -> SessionContext:
    """從 Session 記錄解析內部根、工作目錄與授權根。失敗時回傳穩定錯誤碼。"""

    tenant = current_tenant()
    session = get_session(session_id)
    if session is None:
        raise SessionError("session_not_found", "Session 不存在或不屬於目前 tenant")
    if session["status"] == "archived":
        raise SessionError("session_archived", "歸檔 Session 不能解析新的執行上下文")
    internal = session_root(session_id)
    working = _working_directory(str(session["workingDirectory"] or ""), tenant_root=tenant.data_root)
    roots = []
    for item in list_allowed_roots(session_id):
        path = _existing_directory(item["normalizedPath"], code="directory_unavailable")
        _reject_other_tenant(path, tenant_root=tenant.data_root)
        roots.append(path)
    return SessionContext(
        tenant_id=tenant.tenant_id,
        session_id=session_id,
        session_kind=str(session["kind"] or "chat"),
        session_root=internal,
        working_directory=working,
        allowed_roots=tuple(roots),
        directory_grant_version=int(session["directoryGrantVersion"]),
        resource_grant_version=int(session["resourceGrantVersion"]),
        agent_id=agent_id or str(session["agentId"] or ""),
        run_id=run_id,
        run_snapshot_id=run_snapshot_id,
    )


def _working_directory(value: str, *, tenant_root: Path) -> Path | None:
    if not value:
        return None
    path = _existing_directory(value, code="directory_unavailable")
    _reject_other_tenant(path, tenant_root=tenant_root)
    return path


def _existing_directory(value: str, *, code: str) -> Path:
    path = Path(value).expanduser()
    try:
        resolved = path.resolve()
    except OSError as exc:
        raise SessionError(code, "授權目錄無法解析") from exc
    if path.is_symlink() and not resolved.is_relative_to(path.parent.resolve()):
        raise SessionError(code, "授權目錄經符號連結越界")
    if not resolved.is_dir():
        raise SessionError(code, "授權目錄不存在或不是目錄")
    return resolved


def _reject_other_tenant(path: Path, *, tenant_root: Path) -> None:
    from src.config.runtime_paths import tenants_root

    tenants = tenants_root().resolve()
    if path.is_relative_to(tenants) and not path.is_relative_to(tenant_root.resolve()):
        raise SessionError("tenant_mismatch", "授權目錄不屬於目前 tenant")
