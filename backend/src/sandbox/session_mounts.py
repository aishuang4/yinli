"""Sandbox 的本機工作目錄與 Docker 掛載只來自 Run snapshot 裡的 Session 授權。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.config.runtime_paths import session_artifacts_root
from src.sessions.errors import SessionError
from src.tenancy.context import current_tenant


@dataclass(frozen=True)
class SandboxLayout:
    """一次 Run 的沙箱目錄。cwd 是本機工作目錄，mounts 是容器掛載。"""

    cwd: str
    mounts: tuple[dict[str, str], ...]


def resolve_sandbox_layout(snapshot: dict) -> SandboxLayout:
    """無外部目錄時用 Session artifacts。`/`、越界與 tenant 外路徑都拒絕。"""

    session_id = str(snapshot.get("sessionId") or "")
    roots = _roots(snapshot)
    tenant = current_tenant()
    if tenant.is_hosted:
        boundary = tenant.data_root.resolve()
        for path, _mode in roots:
            if not path.is_relative_to(boundary):
                raise SessionError("illegal_mount", "托管掛載必須在目前 tenant 內")
    cwd = _cwd(snapshot, session_id, roots, hosted=tenant.is_hosted)
    sources = roots or [(cwd, "read_write")]
    mounts: list[dict[str, str]] = []
    workspace_assigned = False
    for index, (path, mode) in enumerate(sources):
        if not path.is_dir():
            raise SessionError("directory_unavailable", "掛載目錄不存在")
        covers_cwd = path == cwd or cwd.is_relative_to(path)
        if covers_cwd and not workspace_assigned:
            target = "/workspace"
            workspace_assigned = True
        else:
            target = f"/mnt/grant-{index}"
        mounts.append({
            "source": str(path),
            "target": target,
            "mode": "ro" if mode == "read" else "rw",
        })
    if not workspace_assigned:
        mounts.insert(0, {"source": str(cwd), "target": "/workspace", "mode": "rw"})
    return SandboxLayout(cwd=str(cwd), mounts=tuple(mounts))


def _roots(snapshot: dict) -> list[tuple[Path, str]]:
    roots: list[tuple[Path, str]] = []
    for item in snapshot.get("allowedRoots") or []:
        raw = str(item.get("normalizedPath") or "").strip()
        if not raw or raw == "/" or ".." in Path(raw).parts:
            raise SessionError("illegal_mount", "掛載路徑不合法")
        mode = str(item.get("accessMode") or "read_write")
        if mode not in {"read", "read_write"}:
            raise SessionError("illegal_mount", "掛載模式不合法")
        path = Path(raw).expanduser()
        try:
            path = path.resolve()
        except OSError as exc:
            raise SessionError("directory_unavailable", "掛載目錄無法解析") from exc
        if path == Path("/").resolve():
            raise SessionError("illegal_mount", "不能掛載根目錄")
        roots.append((path, mode))
    return roots


def _cwd(snapshot: dict, session_id: str, roots: list[tuple[Path, str]], *, hosted: bool) -> Path:
    working = str(snapshot.get("workingDirectory") or "").strip()
    if not working or working == "/":
        root = session_artifacts_root(session_id)
        root.mkdir(parents=True, exist_ok=True)
        return root.resolve()
    cwd = Path(working).expanduser()
    try:
        cwd = cwd.resolve()
    except OSError as exc:
        raise SessionError("directory_unavailable", "工作目錄無法解析") from exc
    if cwd == Path("/").resolve() or not cwd.is_dir():
        raise SessionError("directory_unavailable", "工作目錄不可用")
    if roots and not any(cwd == path or cwd.is_relative_to(path) for path, _mode in roots):
        raise SessionError("illegal_mount", "工作目錄不在授權掛載內")
    if hosted and not cwd.is_relative_to(current_tenant().data_root.resolve()):
        raise SessionError("illegal_mount", "托管工作目錄必須在目前 tenant 內")
    return cwd
