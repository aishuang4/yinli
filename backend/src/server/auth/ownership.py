"""托管模式下把工作台记录限制在当前登录用户。桌面模式不启用。"""

from __future__ import annotations

import uuid
from contextvars import ContextVar
from pathlib import Path

from src.config.runtime_paths import USER_DATA_ROOT
from src.config.settings import settings

_OWNER: ContextVar[str | None] = ContextVar("hosted_owner_user_id", default=None)

# 先删子表。projects 引用 workspaces，group_studios 引用 projects。
OWNED_TABLES = ("group_studios", "chat_sessions", "scheduled_jobs", "projects", "workspaces")


class HostedOwnershipPending(RuntimeError):
    """既有数据还没有归属到某个用户。"""


def current_owner_id() -> str | None:
    """托管请求里的用户。桌面模式或后台任务返回 None。"""

    if settings.serve_mode.strip().lower() != "hosted":
        return None
    return _OWNER.get()


def set_owner(user_id: str | None):
    return _OWNER.set(user_id)


def reset_owner(token) -> None:
    _OWNER.reset(token)


def user_projects_root(user_id: str) -> Path:
    return USER_DATA_ROOT / "users" / user_id / "projects"


def purge_hosted_user(user_id: str) -> None:
    """删除这个托管用户名下的工作区、项目和用户目录。"""

    import shutil

    from src.storage import database

    database.ensure_db()
    with database._lock, database._connect() as conn:
        session_ids = [
            str(row["id"])
            for row in conn.execute(
                "SELECT id FROM chat_sessions WHERE owner_user_id = ?",
                (user_id,),
            ).fetchall()
        ]
        if session_ids:
            marks = ",".join("?" for _ in session_ids)
            conn.execute(
                f"DELETE FROM chat_messages WHERE session_id IN ({marks})",
                session_ids,
            )
        for table in OWNED_TABLES:
            conn.execute(f"DELETE FROM {table} WHERE owner_user_id = ?", (user_id,))
    root = (USER_DATA_ROOT / "users" / user_id).resolve()
    users_root = (USER_DATA_ROOT / "users").resolve()
    if root.is_dir() and root.is_relative_to(users_root) and root.name == user_id:
        shutil.rmtree(root)


def ensure_hosted_home(user_id: str | None = None) -> None:
    """Web 托管用户验证通过后，建立只属于这个人的 Workspace 和 Project。

    桌面端数据留在本机，不调用这里。直接写归属，避免被尚未划给管理员的旧行挡住。
    """

    if settings.serve_mode.strip().lower() != "hosted":
        return
    owner = user_id or current_owner_id()
    if not owner:
        return
    from src.storage import database

    database.ensure_db()
    with database._lock, database._connect() as conn:
        workspace = conn.execute(
            """
            SELECT id FROM workspaces
            WHERE owner_user_id = ? AND status = 'active'
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (owner,),
        ).fetchone()
        if workspace is not None:
            existing = conn.execute(
                """
                SELECT id FROM projects
                WHERE workspace_id = ? AND owner_user_id = ? AND status = 'active'
                LIMIT 1
                """,
                (workspace["id"], owner),
            ).fetchone()
            if existing is not None:
                return
            workspace_id = str(workspace["id"])
        else:
            workspace_id = f"ws_{uuid.uuid4().hex}"
            now = database._utc_now()
            conn.execute(
                """
                INSERT INTO workspaces (
                    id, name, description, status, created_at, updated_at, owner_user_id
                ) VALUES (?, ?, ?, 'active', ?, ?, ?)
                """,
                (workspace_id, "默认工作区", "个人工作区", now, now, owner),
            )
        project_id = f"proj_{uuid.uuid4().hex}"
        root = allocate_project_dir(owner, project_id)
        now = database._utc_now()
        conn.execute(
            """
            INSERT INTO projects (
                id, workspace_id, name, description, root_path,
                instructions_file, status, created_at, updated_at, owner_user_id
            ) VALUES (?, ?, ?, '', ?, '', 'active', ?, ?, ?)
            """,
            (project_id, workspace_id, "默认项目", str(root), now, now, owner),
        )
        database._bind_workspace_resources(
            conn,
            project_id=project_id,
            workspace_id=workspace_id,
            now=now,
        )


def allocate_project_dir(user_id: str, project_id: str) -> Path:
    """在该用户目录下创建 Project 根目录。"""

    root = user_projects_root(user_id) / project_id
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def path_is_inside_user(user_id: str, candidate: Path) -> bool:
    """解析符号链接后，路径必须落在该用户目录内。"""

    try:
        resolved = candidate.expanduser().resolve()
        return resolved.is_relative_to(user_projects_root(user_id).resolve())
    except OSError:
        return False


def unassigned_count() -> int:
    from src.storage import database

    database.ensure_db()
    total = 0
    with database._lock, database._connect() as conn:
        for table in OWNED_TABLES:
            row = conn.execute(
                f"SELECT COUNT(*) AS n FROM {table} WHERE owner_user_id IS NULL OR owner_user_id = ''",
            ).fetchone()
            total += int(row["n"])
    return total


def assert_catalog_ready() -> None:
    """有登录用户时，空归属的旧数据必须先划给某个账号。"""

    if current_owner_id() and unassigned_count():
        raise HostedOwnershipPending("既有数据尚未归属到用户")


def stamp_owner(table: str, row_id: str, user_id: str | None = None) -> None:
    owner = user_id or current_owner_id()
    if not owner or table not in OWNED_TABLES:
        return
    from src.storage import database

    database.ensure_db()
    with database._lock, database._connect() as conn:
        conn.execute(
            f"UPDATE {table} SET owner_user_id = ? WHERE id = ? AND (owner_user_id IS NULL OR owner_user_id = '')",
            (owner, row_id),
        )


def owner_of(table: str, row_id: str) -> str | None:
    from src.storage import database

    database.ensure_db()
    with database._lock, database._connect() as conn:
        row = conn.execute(
            f"SELECT owner_user_id FROM {table} WHERE id = ?",
            (row_id,),
        ).fetchone()
    if row is None:
        return None
    return str(row["owner_user_id"] or "") or None


def visible(table: str, row_id: str) -> bool:
    """无托管用户时全部可见；有用户时只看自己的记录。"""

    owner = current_owner_id()
    if not owner:
        return True
    assert_catalog_ready()
    return owner_of(table, row_id) == owner


def filter_ids(table: str, row_ids: list[str]) -> list[str]:
    owner = current_owner_id()
    if not owner:
        return row_ids
    assert_catalog_ready()
    return [row_id for row_id in row_ids if owner_of(table, row_id) == owner]


def assign_unowned(user_id: str) -> int:
    """把所有尚未归属的业务行划给指定用户。"""

    from src.storage import database

    database.ensure_db()
    changed = 0
    with database._lock, database._connect() as conn:
        exists = conn.execute("SELECT 1 FROM auth_users WHERE id = ?", (user_id,)).fetchone()
        if exists is None:
            raise ValueError(f"用户不存在: {user_id}")
        for table in OWNED_TABLES:
            cursor = conn.execute(
                f"UPDATE {table} SET owner_user_id = ? WHERE owner_user_id IS NULL OR owner_user_id = ''",
                (user_id,),
            )
            changed += cursor.rowcount
    return changed
