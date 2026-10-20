"""Session、目錄 grant、資源 grant 與 Run snapshot 的儲存。"""

from __future__ import annotations

from pathlib import Path
import json
import sqlite3
import uuid
from typing import Any

from src.sessions.errors import SessionError

_ACCESS_MODES = {"read", "read_write"}
_ROOT_SOURCES = {"user", "migration", "hosted"}
_RESOURCE_TYPES = {"agent", "skill", "mcp", "http_tool", "knowledge"}


def _reject_foreign_tenant_path(normalized_path: str) -> None:
    """hosted 不能把別的 tenant 目錄寫進 Session grant。"""

    from src.config.runtime_paths import tenants_root
    from src.tenancy.context import current_tenant

    tenant = current_tenant()
    if not tenant.is_hosted:
        return
    candidate = Path(normalized_path).expanduser()
    try:
        candidate = candidate.resolve()
    except OSError:
        return
    tenants = tenants_root().resolve()
    if candidate.is_relative_to(tenants) and not candidate.is_relative_to(tenant.data_root.resolve()):
        raise SessionError("cross_tenant_path", "不能授權其他 tenant 的目錄")


def _db():
    from src.storage import database

    database.ensure_db()
    return database


def create_session(
    *,
    session_id: str | None = None,
    title: str = "新對話",
    kind: str = "chat",
    agent_id: str = "default",
    agent_name: str = "預設助手",
    working_directory: str = "",
    project_id: str = "",
) -> dict[str, Any]:
    """建立 Session。working_directory 只接受呼叫方明確傳入的值，不建立 Project。"""

    if kind not in {"chat", "job-owner"}:
        raise SessionError("invalid_session_kind", "Session kind 只能是 chat 或 job-owner")
    database = _db()
    now = database._utc_now()
    identifier = session_id or f"ses_{uuid.uuid4().hex}"
    with database._lock, database._connect() as conn:
        conn.execute(
            """
            INSERT INTO chat_sessions (
                id, title, agent_id, agent_name, mode, kind, status,
                working_directory, directory_grant_version, resource_grant_version,
                migration_version, created_at, updated_at, message_count, project_id
            ) VALUES (?, ?, ?, ?, 'agent', ?, 'active', ?, 0, 0, 0, ?, ?, 0, ?)
            """,
            (
                identifier,
                title,
                agent_id,
                agent_name,
                kind,
                working_directory,
                now,
                now,
                project_id,
            ),
        )
        conn.commit()
    session = get_session(identifier)
    if session is None:
        raise SessionError("session_not_found", "Session 建立後無法讀取")
    return session


def get_session(session_id: str) -> dict[str, Any] | None:
    database = _db()
    with database._lock, database._connect() as conn:
        row = conn.execute("SELECT * FROM chat_sessions WHERE id = ?", (session_id,)).fetchone()
    if row is None:
        return None
    return _session_dict(row)


def _session_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "title": row["title"],
        "kind": row["kind"] if "kind" in row.keys() else "chat",
        "status": row["status"] if "status" in row.keys() else "active",
        "agentId": row["agent_id"],
        "workingDirectory": row["working_directory"] if "working_directory" in row.keys() else "",
        "directoryGrantVersion": int(row["directory_grant_version"] or 0) if "directory_grant_version" in row.keys() else 0,
        "resourceGrantVersion": int(row["resource_grant_version"] or 0) if "resource_grant_version" in row.keys() else 0,
        "archivedAt": row["archived_at"] if "archived_at" in row.keys() else None,
        "migrationVersion": int(row["migration_version"] or 0) if "migration_version" in row.keys() else 0,
        "projectId": row["project_id"] if "project_id" in row.keys() else "",
        "workspaceId": row["workspace_id"] if "workspace_id" in row.keys() else "",
    }


def add_allowed_root(
    session_id: str,
    normalized_path: str,
    *,
    access_mode: str,
    source: str,
) -> dict[str, Any]:
    """新增目錄授權。同一 Session 的規範路徑唯一，並遞增 directory grant 版本。"""

    if access_mode not in _ACCESS_MODES:
        raise SessionError("invalid_access_mode", "access_mode 只能是 read 或 read_write")
    if source not in _ROOT_SOURCES:
        raise SessionError("invalid_root_source", "目錄來源不在允許集合")
    _reject_foreign_tenant_path(normalized_path)
    database = _db()
    now = database._utc_now()
    with database._lock, database._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        session = conn.execute("SELECT id FROM chat_sessions WHERE id = ?", (session_id,)).fetchone()
        if session is None:
            conn.rollback()
            raise SessionError("session_not_found", "Session 不存在")
        try:
            conn.execute(
                """
                INSERT INTO session_allowed_roots (
                    session_id, normalized_path, access_mode, source, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (session_id, normalized_path, access_mode, source, now),
            )
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            raise SessionError("duplicate_allowed_root", "該路徑已授權") from exc
        version = _bump(conn, session_id, "directory_grant_version", now)
        conn.commit()
    return {"sessionId": session_id, "normalizedPath": normalized_path, "directoryGrantVersion": version}


def list_allowed_roots(session_id: str) -> list[dict[str, Any]]:
    database = _db()
    with database._lock, database._connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM session_allowed_roots
            WHERE session_id = ? ORDER BY normalized_path
            """,
            (session_id,),
        ).fetchall()
    return [
        {
            "sessionId": row["session_id"],
            "normalizedPath": row["normalized_path"],
            "accessMode": row["access_mode"],
            "source": row["source"],
            "createdAt": row["created_at"],
        }
        for row in rows
    ]


def upsert_resource_grant(
    session_id: str,
    resource_type: str,
    resource_id: str,
    *,
    enabled: bool = True,
    config_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """寫入一筆資源 grant，並把 Session resource_grant_version 加一。"""

    if resource_type not in _RESOURCE_TYPES:
        raise SessionError("invalid_resource_type", "不支援的資源類型")
    database = _db()
    now = database._utc_now()
    override = json.dumps(config_override or {}, ensure_ascii=False, sort_keys=True)
    with database._lock, database._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT 1 FROM chat_sessions WHERE id = ?", (session_id,)).fetchone() is None:
            conn.rollback()
            raise SessionError("session_not_found", "Session 不存在")
        version = _bump(conn, session_id, "resource_grant_version", now)
        conn.execute(
            """
            INSERT INTO session_resource_grants (
                session_id, resource_type, resource_id, enabled, config_override,
                grant_version, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id, resource_type, resource_id) DO UPDATE SET
                enabled = excluded.enabled,
                config_override = excluded.config_override,
                grant_version = excluded.grant_version,
                updated_at = excluded.updated_at
            """,
            (session_id, resource_type, resource_id, 1 if enabled else 0, override, version, now, now),
        )
        conn.commit()
    return {
        "sessionId": session_id,
        "resourceType": resource_type,
        "resourceId": resource_id,
        "enabled": enabled,
        "grantVersion": version,
    }


def list_resource_grants(session_id: str) -> list[dict[str, Any]]:
    database = _db()
    with database._lock, database._connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM session_resource_grants
            WHERE session_id = ? ORDER BY resource_type, resource_id
            """,
            (session_id,),
        ).fetchall()
    return [
        {
            "sessionId": row["session_id"],
            "resourceType": row["resource_type"],
            "resourceId": row["resource_id"],
            "enabled": bool(row["enabled"]),
            "configOverride": json.loads(row["config_override"] or "{}"),
            "grantVersion": int(row["grant_version"]),
        }
        for row in rows
    ]


def _bump(conn: sqlite3.Connection, session_id: str, column: str, now: str) -> int:
    row = conn.execute(f"SELECT {column} FROM chat_sessions WHERE id = ?", (session_id,)).fetchone()
    version = int(row[0] or 0) + 1
    conn.execute(
        f"UPDATE chat_sessions SET {column} = ?, updated_at = ? WHERE id = ?",
        (version, now, session_id),
    )
    return version


def freeze_run_snapshot(
    *,
    run_id: str,
    session_id: str,
    working_directory: str,
    allowed_roots: list[dict[str, Any]],
    resources: dict[str, Any],
    model_snapshot: dict[str, Any],
    approval_snapshot: dict[str, Any],
    agent_snapshot: dict[str, Any] | None = None,
    snapshot_id: str | None = None,
) -> dict[str, Any]:
    """凍結 Run 當下的目錄、資源、模型與審批。已存在的 snapshot 不會被改寫。"""

    database = _db()
    now = database._utc_now()
    identifier = snapshot_id or f"snap_{uuid.uuid4().hex}"
    with database._lock, database._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        session = conn.execute(
            """
            SELECT directory_grant_version, resource_grant_version
            FROM chat_sessions WHERE id = ?
            """,
            (session_id,),
        ).fetchone()
        if session is None:
            conn.rollback()
            raise SessionError("session_not_found", "Session 不存在")
        existing = conn.execute(
            "SELECT id FROM run_context_snapshots WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if existing is not None:
            conn.rollback()
            return get_run_snapshot(run_id) or {}
        conn.execute(
            """
            INSERT INTO run_context_snapshots (
                id, run_id, session_id, directory_grant_version, resource_grant_version,
                working_directory, allowed_roots_json, resources_json, model_snapshot_json,
                approval_snapshot_json, agent_snapshot_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                identifier,
                run_id,
                session_id,
                int(session["directory_grant_version"] or 0),
                int(session["resource_grant_version"] or 0),
                working_directory,
                json.dumps(allowed_roots, ensure_ascii=False, sort_keys=True),
                json.dumps(resources, ensure_ascii=False, sort_keys=True),
                json.dumps(model_snapshot, ensure_ascii=False, sort_keys=True),
                json.dumps(approval_snapshot, ensure_ascii=False, sort_keys=True),
                json.dumps(agent_snapshot or {}, ensure_ascii=False, sort_keys=True),
                now,
            ),
        )
        conn.commit()
    snapshot = get_run_snapshot(run_id)
    if snapshot is None:
        raise SessionError("snapshot_missing", "Run snapshot 寫入後無法讀取")
    return snapshot


def get_run_snapshot(run_id: str) -> dict[str, Any] | None:
    database = _db()
    with database._lock, database._connect() as conn:
        row = conn.execute(
            "SELECT * FROM run_context_snapshots WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "runId": row["run_id"],
        "sessionId": row["session_id"],
        "directoryGrantVersion": int(row["directory_grant_version"]),
        "resourceGrantVersion": int(row["resource_grant_version"]),
        "workingDirectory": row["working_directory"],
        "allowedRoots": json.loads(row["allowed_roots_json"] or "[]"),
        "resources": json.loads(row["resources_json"] or "{}"),
        "modelSnapshot": json.loads(row["model_snapshot_json"] or "{}"),
        "approvalSnapshot": json.loads(row["approval_snapshot_json"] or "{}"),
        "agentSnapshot": json.loads(row["agent_snapshot_json"] or "{}"),
        "createdAt": row["created_at"],
    }
