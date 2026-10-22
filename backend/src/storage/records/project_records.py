"""Project 与目录授权的 SQLite 存储。身份是 slug，不再有 Workspace。"""
from __future__ import annotations


import sqlite3
import unicodedata
from collections.abc import Callable
from pathlib import Path
from typing import Any

RESERVED_SLUGS = frozenset({
    "default",
    "_system",
    "tmp",
    "sessions",
    "jobs",
    "backups",
    "exports",
    "agents",
    "skills",
    "data",
})
_FORBIDDEN_NAME_CHARS = set('/\\:*?"<>|')


class ProjectNameError(ValueError):
    """Project 名称或 slug 不合法。"""


class ProjectRecordStore:
    """读写 projects 与 project_folders。"""

    def __init__(
        self,
        *,
        ensure_db: Callable[[], None],
        connect: Callable[[], sqlite3.Connection],
        lock: Any,
        utc_now: Callable[[], str],
        default_root: Callable[[str], Path],
        hosted: Callable[[], bool],
    ) -> None:
        self.ensure_db = ensure_db
        self.connect = connect
        self.lock = lock
        self.utc_now = utc_now
        self.default_root = default_root
        self.hosted = hosted

    def list_projects(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        self.ensure_db()
        query = "SELECT * FROM projects"
        if not include_archived:
            query += " WHERE status = 'active'"
        query += " ORDER BY updated_at DESC, slug ASC"
        with self.lock, self.connect() as conn:
            rows = conn.execute(query).fetchall()
            return [self._project_from_row(conn, row) for row in rows]

    def get_project(self, slug: str) -> dict[str, Any] | None:
        self.ensure_db()
        with self.lock, self.connect() as conn:
            row = conn.execute("SELECT * FROM projects WHERE slug = ?", (slug,)).fetchone()
            return self._project_from_row(conn, row) if row else None

    def create_project(self, data: dict[str, Any]) -> dict[str, Any]:
        self.ensure_db()
        name = validate_project_name(str(data.get("name") or ""))
        slug = slugify_project_name(name)
        if slug in RESERVED_SLUGS:
            raise ProjectNameError(f"名称保留，不能使用: {name}")
        primary = self._primary_path(slug, data.get("primaryPath") or data.get("primary_path"))
        now = self.utc_now()
        with self.lock, self.connect() as conn:
            self._reject_duplicate(conn, name, slug)
            conn.execute(
                """
                INSERT INTO projects (
                    slug, name, primary_path, color, icon, status, created_at, updated_at, archived_at
                ) VALUES (?, ?, ?, ?, ?, 'active', ?, ?, NULL)
                """,
                (slug, name, str(primary), data.get("color"), data.get("icon"), now, now),
            )
            self._insert_folder(conn, slug, primary, "readwrite", is_primary=True, granted_at=now)
            for folder in data.get("folders") or []:
                if self.hosted():
                    raise ProjectNameError("托管模式不接受额外目录")
                path = _realpath(str(folder.get("path") or ""))
                if path == primary:
                    continue
                mode = str(folder.get("accessMode") or folder.get("access_mode") or "read")
                if mode not in {"read", "readwrite"}:
                    raise ProjectNameError(f"未知的目录权限: {mode}")
                self._insert_folder(conn, slug, path, mode, is_primary=False, granted_at=now)
            conn.commit()
        created = self.get_project(slug)
        if created is None:
            raise ProjectNameError("Project 写入后无法读取")
        return created

    def archive_project(self, slug: str) -> dict[str, Any] | None:
        if slug == "default":
            raise ProjectNameError("默认项目不能归档")
        self.ensure_db()
        now = self.utc_now()
        with self.lock, self.connect() as conn:
            updated = conn.execute(
                """
                UPDATE projects
                SET status = 'archived', archived_at = ?, updated_at = ?
                WHERE slug = ? AND status = 'active'
                """,
                (now, now, slug),
            ).rowcount
            conn.commit()
        if not updated:
            return self.get_project(slug)
        return self.get_project(slug)

    def update_appearance(
        self,
        slug: str,
        *,
        color: str | None = None,
        icon: str | None = None,
        set_color: bool = False,
        set_icon: bool = False,
    ) -> dict[str, Any] | None:
        """只更新颜色和图标。名称创建后不可改。"""

        self.ensure_db()
        assignments: list[str] = []
        params: list[Any] = []
        if set_color:
            assignments.append("color = ?")
            params.append(color)
        if set_icon:
            assignments.append("icon = ?")
            params.append(icon)
        if not assignments:
            return self.get_project(slug)
        now = self.utc_now()
        assignments.append("updated_at = ?")
        params.extend([now, slug])
        with self.lock, self.connect() as conn:
            updated = conn.execute(
                f"UPDATE projects SET {', '.join(assignments)} WHERE slug = ?",
                params,
            ).rowcount
            conn.commit()
        if not updated:
            return None
        return self.get_project(slug)

    def add_folder(self, slug: str, path: str, access_mode: str) -> dict[str, Any] | None:
        """给桌面 Project 追加一条目录授权。托管模式由路由直接拒绝。"""

        if self.hosted():
            raise ProjectNameError("托管模式不接受额外目录")
        if access_mode not in {"read", "readwrite"}:
            raise ProjectNameError(f"未知的目录权限: {access_mode}")
        target = assert_directory_grant(path)
        self.ensure_db()
        now = self.utc_now()
        with self.lock, self.connect() as conn:
            project = conn.execute("SELECT slug, status FROM projects WHERE slug = ?", (slug,)).fetchone()
            if project is None or project["status"] != "active":
                return None
            existing = conn.execute(
                "SELECT path FROM project_folders WHERE project_slug = ? AND path = ?",
                (slug, str(target)),
            ).fetchone()
            if existing is None:
                self._insert_folder(conn, slug, target, access_mode, is_primary=False, granted_at=now)
            else:
                conn.execute(
                    """
                    UPDATE project_folders SET access_mode = ?
                    WHERE project_slug = ? AND path = ?
                    """,
                    (access_mode, slug, str(target)),
                )
            conn.execute("UPDATE projects SET updated_at = ? WHERE slug = ?", (now, slug))
            conn.commit()
        return self.get_project(slug)

    def remove_folder(self, slug: str, path: str) -> dict[str, Any] | None:
        """移除非主目錄授權。主目錄保留。"""

        if self.hosted():
            raise ProjectNameError("托管模式不接受额外目录")
        target = assert_directory_grant(path)
        self.ensure_db()
        now = self.utc_now()
        with self.lock, self.connect() as conn:
            project = conn.execute("SELECT slug, status FROM projects WHERE slug = ?", (slug,)).fetchone()
            if project is None or project["status"] != "active":
                return None
            row = conn.execute(
                "SELECT is_primary FROM project_folders WHERE project_slug = ? AND path = ?",
                (slug, str(target)),
            ).fetchone()
            if row is None:
                raise ProjectNameError("目录不在授权列表中")
            if bool(row["is_primary"]):
                raise ProjectNameError("主目录不能移除")
            conn.execute(
                "DELETE FROM project_folders WHERE project_slug = ? AND path = ?",
                (slug, str(target)),
            )
            conn.execute("UPDATE projects SET updated_at = ? WHERE slug = ?", (now, slug))
            conn.commit()
        return self.get_project(slug)

    def delete_project(self, slug: str) -> bool:
        """删除非默认 Project。默认项目保留。"""

        if slug == "default":
            raise ProjectNameError("默认项目不能删除")
        self.ensure_db()
        with self.lock, self.connect() as conn:
            deleted = conn.execute("DELETE FROM projects WHERE slug = ?", (slug,)).rowcount
            conn.commit()
        return bool(deleted)

    def _primary_path(self, slug: str, requested: object) -> Path:
        if self.hosted() or not requested:
            path = self.default_root(slug)
            path.mkdir(parents=True, exist_ok=True)
            return path.resolve()
        return _realpath(str(requested))

    def _reject_duplicate(self, conn: sqlite3.Connection, name: str, slug: str) -> None:
        row = conn.execute(
            "SELECT slug FROM projects WHERE slug = ? OR name = ? COLLATE NOCASE",
            (slug, name),
        ).fetchone()
        if row is not None:
            raise ProjectNameError(f"Project 名称或 slug 已存在: {name}")

    def _insert_folder(
        self,
        conn: sqlite3.Connection,
        slug: str,
        path: Path,
        access_mode: str,
        *,
        is_primary: bool,
        granted_at: str,
    ) -> None:
        device_id, inode = _path_identity(path)
        conn.execute(
            """
            INSERT INTO project_folders (
                project_slug, path, access_mode, is_primary, device_id, inode, granted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (slug, str(path), access_mode, int(is_primary), device_id, inode, granted_at),
        )

    def _project_from_row(self, conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        folders = conn.execute(
            """
            SELECT path, access_mode, is_primary, granted_at
            FROM project_folders WHERE project_slug = ? ORDER BY is_primary DESC, path ASC
            """,
            (row["slug"],),
        ).fetchall()
        return {
            "slug": row["slug"],
            "name": row["name"],
            "primaryPath": row["primary_path"],
            "color": row["color"],
            "icon": row["icon"],
            "status": row["status"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "archivedAt": row["archived_at"],
            "folders": [
                {
                    "path": item["path"],
                    "accessMode": item["access_mode"],
                    "isPrimary": bool(item["is_primary"]),
                    "grantedAt": item["granted_at"],
                }
                for item in folders
            ],
        }


def validate_project_name(name: str) -> str:
    """校验用户输入的 Project 名称。"""

    raw = unicodedata.normalize("NFC", name)
    if raw != raw.strip() or not raw.strip() or raw.strip().startswith(".") or raw.strip() == "..":
        raise ProjectNameError("名称不能为空、不能以点或空白开头")
    text = raw.strip()
    if any(char in _FORBIDDEN_NAME_CHARS for char in text) or len(text) > 64:
        raise ProjectNameError("名称含有非法字符或超过 64 字")
    return text


def slugify_project_name(name: str) -> str:
    """由名称派生 slug。冲突由调用方拒绝，不自动加后缀。"""

    text = unicodedata.normalize("NFC", name).strip().lower()
    chars: list[str] = []
    previous_dash = False
    for char in text:
        if char.isspace():
            if chars and not previous_dash:
                chars.append("-")
                previous_dash = True
            continue
        if char.isascii() and not (char.isalnum() or char in {"-", "_"}):
            continue
        chars.append(char)
        previous_dash = char == "-"
    slug = "".join(chars).strip("-")[:64].strip("-")
    if not slug:
        raise ProjectNameError("名称无法生成 slug")
    return slug


def assert_directory_grant(value: str) -> Path:
    """校验授权目录：绝对路径、不含 ..、路径上没有符号链接、且目录存在。"""

    path = Path(unicodedata.normalize("NFC", value)).expanduser()
    if not path.is_absolute():
        raise ProjectNameError("目录必须是绝对路径")
    if ".." in path.parts:
        raise ProjectNameError("目录不能包含 ..")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        if current.is_symlink():
            raise ProjectNameError("目录路径不能经过符号链接")
    if not current.is_dir():
        raise ProjectNameError("目录不存在")
    return current


def _realpath(value: str) -> Path:
    path = Path(unicodedata.normalize("NFC", value)).expanduser()
    if not path.is_absolute():
        raise ProjectNameError("目录必须是绝对路径")
    if ".." in path.parts:
        raise ProjectNameError("目录不能包含 ..")
    return path.resolve()


def _path_identity(path: Path) -> tuple[str, str]:
    try:
        stat = path.stat()
    except OSError:
        return "", ""
    return str(getattr(stat, "st_dev", "") or ""), str(getattr(stat, "st_ino", "") or "")

from src.storage.engine.database import _connect, _lock, _utc_now, ensure_db

def _project_record_store() -> ProjectRecordStore:
    """Project 以 slug 为身份。"""

    from src.config import runtime_paths
    from src.tenancy.context import current_tenant

    return ProjectRecordStore(
        ensure_db=ensure_db,
        connect=_connect,
        lock=_lock,
        utc_now=_utc_now,
        default_root=runtime_paths.project_default_root,
        hosted=lambda: current_tenant().is_hosted,
    )

def list_projects(*, include_archived: bool = False) -> list[dict[str, Any]]:
    """列出 Project。"""

    return _project_record_store().list_projects(include_archived=include_archived)

def get_project(project_id: str) -> dict[str, Any] | None:
    """按 slug 读取 Project。"""

    return _project_record_store().get_project(project_id)

def create_project(data: dict[str, Any]) -> dict[str, Any]:
    """创建 Project。名称派生 slug，重名直接拒绝。"""

    payload = dict(data)
    if not payload.get("primaryPath") and not payload.get("primary_path"):
        payload["primaryPath"] = payload.get("rootPath") or payload.get("root_path")
    return _project_record_store().create_project(payload)

def update_project(project_id: str, changes: dict[str, Any]) -> dict[str, Any] | None:
    """归档 Project。名称创建后不可改。"""

    store = _project_record_store()
    if str(changes.get("status") or "") == "archived":
        return store.archive_project(project_id)
    if "color" in changes or "icon" in changes:
        return store.update_appearance(
            project_id,
            color=changes.get("color"),
            icon=changes.get("icon"),
            set_color="color" in changes,
            set_icon="icon" in changes,
        )
    return get_project(project_id)

def add_project_folder(slug: str, path: str, access_mode: str = "read") -> dict[str, Any] | None:
    """追加一条目录授权。"""

    return _project_record_store().add_folder(slug, path, access_mode)

def remove_project_folder(slug: str, path: str) -> dict[str, Any] | None:
    """移除非主目录授权。"""

    return _project_record_store().remove_folder(slug, path)

def delete_project(slug: str) -> bool:
    """删除非默认 Project。"""

    return _project_record_store().delete_project(slug)

def get_chat_session_location(session_id: str) -> dict[str, str] | None:
    """读取 Session 所在的 Workspace 和 Project。"""

    from src.storage.records.chat_session_records import _chat_session_record_store

    return _chat_session_record_store().get_location(session_id)
