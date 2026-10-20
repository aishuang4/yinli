"""Agent filesystem scope 與 Session allowed roots 的規範交集。

空範圍、`/`、進程 cwd 和 default project root 都不是授權。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.sessions.errors import SessionError


@dataclass(frozen=True)
class AllowedDirectory:
    """一個已規範化的 Session 目錄授權。"""

    path: Path
    access_mode: str


@dataclass(frozen=True)
class FilesystemGrant:
    """交集後的讀寫根。寫入根是讀取根的子集。"""

    read_roots: tuple[Path, ...]
    write_roots: tuple[Path, ...]


def _is_unset(value: str | None) -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    try:
        resolved = Path(text).expanduser().resolve()
    except OSError:
        return True
    return resolved == Path("/").resolve()


def _resolve_dir(value: str | Path) -> Path:
    path = Path(value).expanduser()
    try:
        resolved = path.resolve()
    except OSError as exc:
        raise SessionError("directory_unavailable", "目錄無法解析") from exc
    if not resolved.is_dir():
        raise SessionError("directory_unavailable", "目錄不存在或不是目錄")
    return resolved


def intersect_filesystem(
    agent_scope: str | None,
    allowed: list[AllowedDirectory],
) -> FilesystemGrant:
    """計算規範交集。沒有 Session 授權，或與 Agent scope 不相交時失敗。"""

    grants = [
        AllowedDirectory(_resolve_dir(item.path), item.access_mode)
        for item in allowed
        if item.access_mode in {"read", "read_write"}
    ]
    if not grants:
        raise SessionError("filesystem_scope_disjoint", "Session 沒有目錄授權")
    if _is_unset(agent_scope):
        narrowed = grants
    else:
        agent = _resolve_dir(str(agent_scope))
        narrowed = []
        for item in grants:
            if item.path == agent or item.path.is_relative_to(agent):
                narrowed.append(item)
            elif agent.is_relative_to(item.path):
                narrowed.append(AllowedDirectory(agent, item.access_mode))
        if not narrowed:
            raise SessionError("filesystem_scope_disjoint", "Agent filesystem scope 與 Session allowed roots 不相交")
    return FilesystemGrant(
        read_roots=tuple(item.path for item in narrowed),
        write_roots=tuple(item.path for item in narrowed if item.access_mode == "read_write"),
    )


def authorize_path(path: str | Path, grant: FilesystemGrant, *, write: bool) -> Path:
    """解析真實路徑。唯讀根拒絕寫入，符號連結不得越出授權根。"""

    original = Path(path).expanduser()
    try:
        resolved = original.resolve()
    except OSError as exc:
        raise SessionError("filesystem_scope_denied", "路徑無法解析") from exc
    roots = grant.write_roots if write else grant.read_roots
    if any(resolved == root or resolved.is_relative_to(root) for root in roots):
        return resolved
    if write and any(resolved == root or resolved.is_relative_to(root) for root in grant.read_roots):
        raise SessionError("filesystem_readonly", "該目錄只有讀取授權")
    raise SessionError("filesystem_scope_denied", "路徑超出授權目錄")
