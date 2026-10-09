"""Workspace、Project 与 Project 资源绑定的 SQLite 领域存储。"""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any


def _keep(table: str, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from src.server.auth.ownership import filter_ids

    allowed = set(filter_ids(table, [str(item["id"]) for item in records]))
    return [item for item in records if str(item["id"]) in allowed]


def _visible(table: str, row_id: str) -> bool:
    from src.server.auth.ownership import visible

    return visible(table, row_id)


def _stamp(table: str, row_id: str) -> None:
    from src.server.auth.ownership import stamp_owner

    stamp_owner(table, row_id)


class WorkspaceProjectRecordStore:
    """封装 Workspace、Project 及其资源白名单的领域读写。"""

    def __init__(
        self,
        *,
        ensure_db: Callable[[], None],
        connect: Callable[[], sqlite3.Connection],
        lock: Any,
        utc_now: Callable[[], str],
        dumps: Callable[[Any], str],
        loads: Callable[[str | None, Any], Any],
        bind_workspace_resources: Callable[..., None],
        resource_tables: Mapping[str, str],
        default_workspace_id: str,
        project_root: Path,
    ) -> None:
        self.ensure_db = ensure_db
        self.connect = connect
        self.lock = lock
        self.utc_now = utc_now
        self.dumps = dumps
        self.loads = loads
        self.bind_workspace_resources = bind_workspace_resources
        self.resource_tables = resource_tables
        self.default_workspace_id = default_workspace_id
        self.project_root = project_root

    @staticmethod
    def _workspace_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "status": row["status"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    @staticmethod
    def _project_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "workspaceId": row["workspace_id"],
            "name": row["name"],
            "description": row["description"],
            "rootPath": row["root_path"],
            "instructionsFile": row["instructions_file"],
            "status": row["status"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    def list_workspaces(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        self.ensure_db()
        query = "SELECT * FROM workspaces"
        params: tuple[Any, ...] = ()
        if not include_archived:
            query += " WHERE status = ?"
            params = ("active",)
        query += " ORDER BY updated_at DESC, id ASC"
        with self.lock, self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
        records = [self._workspace_from_row(row) for row in rows]
        return _keep("workspaces", records)

    def get_workspace(self, workspace_id: str) -> dict[str, Any] | None:
        self.ensure_db()
        with self.lock, self.connect() as conn:
            row = conn.execute("SELECT * FROM workspaces WHERE id = ?", (workspace_id,)).fetchone()
        record = self._workspace_from_row(row) if row else None
        if record is None or not _visible("workspaces", str(record["id"])):
            return None
        return record

    def create_workspace(self, data: dict[str, Any]) -> dict[str, Any]:
        self.ensure_db()
        now = self.utc_now()
        workspace_id = str(data.get("id") or f"ws_{uuid.uuid4().hex}")
        with self.lock, self.connect() as conn:
            conn.execute(
                """
                INSERT INTO workspaces (id, name, description, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    workspace_id,
                    str(data.get("name") or "未命名工作区"),
                    str(data.get("description") or ""),
                    str(data.get("status") or "active"),
                    now,
                    now,
                ),
            )
            conn.commit()
        _stamp("workspaces", workspace_id)
        return self.get_workspace(workspace_id) or {}

    def list_projects(
        self,
        workspace_id: str | None = None,
        *,
        include_archived: bool = False,
    ) -> list[dict[str, Any]]:
        self.ensure_db()
        clauses: list[str] = []
        params: list[Any] = []
        if workspace_id:
            clauses.append("workspace_id = ?")
            params.append(workspace_id)
        if not include_archived:
            clauses.append("status = 'active'")
        query = "SELECT * FROM projects"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY updated_at DESC, id ASC"
        with self.lock, self.connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return _keep("projects", [self._project_from_row(row) for row in rows])

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        self.ensure_db()
        with self.lock, self.connect() as conn:
            row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        record = self._project_from_row(row) if row else None
        if record is None or not _visible("projects", str(record["id"])):
            return None
        return record

    def create_project(self, data: dict[str, Any]) -> dict[str, Any]:
        self.ensure_db()
        now = self.utc_now()
        project_id = str(data.get("id") or f"proj_{uuid.uuid4().hex}")
        workspace_id = str(
            data.get("workspace_id") or data.get("workspaceId") or self.default_workspace_id,
        )
        with self.lock, self.connect() as conn:
            workspace = conn.execute("SELECT id FROM workspaces WHERE id = ?", (workspace_id,)).fetchone()
            if workspace is None:
                raise ValueError(f"Workspace 不存在: {workspace_id}")
            conn.execute(
                """
                INSERT INTO projects (
                    id, workspace_id, name, description, root_path,
                    instructions_file, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    workspace_id,
                    str(data.get("name") or "未命名项目"),
                    str(data.get("description") or ""),
                    str(data.get("root_path") or data.get("rootPath") or self.project_root),
                    str(data.get("instructions_file") or data.get("instructionsFile") or ""),
                    str(data.get("status") or "active"),
                    now,
                    now,
                ),
            )
            self.bind_workspace_resources(
                conn,
                project_id=project_id,
                workspace_id=workspace_id,
                now=now,
            )
            conn.commit()
        _stamp("projects", project_id)
        return self.get_project(project_id) or {}

    def update_project(self, project_id: str, changes: dict[str, Any]) -> dict[str, Any] | None:
        self.ensure_db()
        existing = self.get_project(project_id)
        if existing is None:
            return None
        values = {
            "name": str(changes.get("name", existing["name"])),
            "description": str(changes.get("description", existing["description"])),
            "root_path": str(changes.get("root_path", changes.get("rootPath", existing["rootPath"]))),
            "instructions_file": str(
                changes.get(
                    "instructions_file",
                    changes.get("instructionsFile", existing["instructionsFile"]),
                ),
            ),
            "status": str(changes.get("status", existing["status"])),
        }
        with self.lock, self.connect() as conn:
            conn.execute(
                """
                UPDATE projects
                SET name = ?, description = ?, root_path = ?, instructions_file = ?,
                    status = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    values["name"],
                    values["description"],
                    values["root_path"],
                    values["instructions_file"],
                    values["status"],
                    self.utc_now(),
                    project_id,
                ),
            )
            conn.commit()
        return self.get_project(project_id)

    def list_resource_bindings(self, project_id: str) -> list[dict[str, Any]]:
        self.ensure_db()
        with self.lock, self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM project_resource_bindings
                WHERE project_id = ?
                ORDER BY resource_type, resource_id
                """,
                (project_id,),
            ).fetchall()
        return [
            {
                "projectId": row["project_id"],
                "resourceType": row["resource_type"],
                "resourceId": row["resource_id"],
                "enabled": bool(row["enabled"]),
                "configOverride": self.loads(row["config_override"], {}),
                "createdAt": row["created_at"],
                "updatedAt": row["updated_at"],
            }
            for row in rows
        ]

    def get_workspace_resource(self, resource_type: str, resource_id: str) -> dict[str, str] | None:
        self.ensure_db()
        table = self.resource_tables.get(resource_type)
        if table is None:
            return None
        with self.lock, self.connect() as conn:
            row = conn.execute(
                f"SELECT id, workspace_id FROM {table} WHERE id = ?",
                (resource_id,),
            ).fetchone()
            if row is None and resource_type == "http_tool":
                legacy = conn.execute(
                    "SELECT key FROM json_store WHERE namespace = 'knowledge_tools' AND key = ?",
                    (resource_id,),
                ).fetchone()
                if legacy is not None:
                    return {"id": str(legacy["key"]), "workspaceId": self.default_workspace_id}
        if row is None:
            return None
        return {
            "id": str(row["id"]),
            "workspaceId": str(row["workspace_id"] or self.default_workspace_id),
        }

    def upsert_resource_binding(self, data: dict[str, Any]) -> dict[str, Any]:
        self.ensure_db()
        project_id = str(data.get("project_id") or data.get("projectId") or "")
        resource_type = str(data.get("resource_type") or data.get("resourceType") or "")
        resource_id = str(data.get("resource_id") or data.get("resourceId") or "")
        now = self.utc_now()
        with self.lock, self.connect() as conn:
            if conn.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone() is None:
                raise ValueError(f"Project 不存在: {project_id}")
            conn.execute(
                """
                INSERT INTO project_resource_bindings (
                    project_id, resource_type, resource_id, enabled,
                    config_override, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, resource_type, resource_id) DO UPDATE SET
                    enabled = excluded.enabled,
                    config_override = excluded.config_override,
                    updated_at = excluded.updated_at
                """,
                (
                    project_id,
                    resource_type,
                    resource_id,
                    int(bool(data.get("enabled", True))),
                    self.dumps(data.get("config_override") or data.get("configOverride") or {}),
                    now,
                    now,
                ),
            )
            conn.commit()
        return next(
            item
            for item in self.list_resource_bindings(project_id)
            if item["resourceType"] == resource_type and item["resourceId"] == resource_id
        )
