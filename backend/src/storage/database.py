"""
SQLite-backed JSON document storage.

The current route models are Pydantic documents that were previously persisted
as JSON files. This module stores the same documents in SQLite so the API
surface can stay stable while persistence moves off flat files.
"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any

from src.config.runtime_paths import (
    DATA_ROOT,
    DEFAULT_PROJECT_ROOT,
    INSTALL_BACKEND_ROOT,
    USING_CUSTOM_DATA_ROOT,
    USER_DATA_ROOT,
    ensure_runtime_directories,
)
from src.config.settings import settings
from src.storage.chat_run_records import ChatRunRecordStore
from src.storage.database_schema import BASE_SCHEMA_SQL
from src.storage.scheduled_job_records import ScheduledJobRecordStore
from src.storage.agent_defaults import (
    is_stale_desktop_default_project_path as _is_stale_desktop_default_project_path,
    normalize_guardrails,
)
from src.storage.json_document_store import JsonDocumentStore
from src.storage.memory_records import MemoryRecordStore
from src.storage.plan_records import PlanRecordStore
from src.storage.resource_catalog_records import ResourceCatalogRecordStore
from src.storage.memory_markdown_candidates import MemoryMarkdownCandidateStore
from src.storage.session_context_state import SessionContextStateStore
from src.storage.skill_run_records import SkillRunRecordStore
from src.storage.skill_evolution_records import SkillEvolutionRecordStore
from src.storage.trace_records import TraceRecordStore
from src.storage.group_studio_records import GroupStudioRecordStore
from src.storage.workflow_records import WorkflowRecordStore
from src.storage.workspace_project_records import WorkspaceProjectRecordStore


_lock = RLock()
_initialized = False
BACKEND_ROOT = INSTALL_BACKEND_ROOT
PROJECT_ROOT = DEFAULT_PROJECT_ROOT
DEFAULT_WORKSPACE_ID = "default"
DEFAULT_PROJECT_ID = "default"
PROJECT_RESOURCE_TYPES = ("agent", "skill", "mcp", "http_tool")
_PROJECT_RESOURCE_TABLES = {
    "agent": "agents",
    "skill": "skills",
    "mcp": "mcp_servers",
    "http_tool": "http_tools",
}
_RESOURCE_BINDING_MIGRATION_KEY = "project_resource_bindings_v1"

DEFAULT_GUARDRAILS = {
    "filesystem_scope": str(PROJECT_ROOT),
    "shell_blacklist": ["rm -rf /", "sudo"],
    "network_enabled": True,
    "human_approval": False,
    "tool_permission_mode": "accept_edits",
    "tool_permission_profile": "confirm_destructive",
    "execution_backend": "local",
    "docker_image": "python:3.11-slim",
    "persistent_sandbox": False,
    "timeout_seconds": 300,
}


def _normalize_guardrails(data: dict[str, Any]) -> dict[str, Any]:
    """归一化历史 guardrails 默认值，避免旧 '/' 继续暴露给前端。"""

    return normalize_guardrails(
        data,
        defaults=DEFAULT_GUARDRAILS,
        project_root=PROJECT_ROOT,
    )

DEFAULT_PLAN_CONFIG = {
    "enabled": False,
    "mode": "structured",
    "maxSteps": 6,
    "requirePlanBeforeTools": False,
    "requireUserApproval": False,
    "allowReplanning": False,
}

DEFAULT_MODEL_CONFIG = {
    "primary": "deepseek-v4-flash",
    "fallback": ["deepseek-v4-pro"],
    "temperature": 0.7,
    "topP": 0.8,
    "maxTokens": 4096,
    "maxReactIters": 40,
}
EMPTY_DISPLAY_SNAPSHOT = {
    "version": 4,
    "lastSeq": 0,
    "streamDone": False,
    "order": [],
    "blocksById": {},
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db_path() -> Path:
    url = settings.database_url
    prefix = "sqlite+aiosqlite:///"
    if url.startswith(prefix):
        return _resolve_sqlite_path(url[len(prefix):])
    if url.startswith("sqlite:///"):
        return _resolve_sqlite_path(url[len("sqlite:///"):])
    return DATA_ROOT / "agent_workstation.db"


def _resolve_sqlite_path(value: str) -> Path:
    """把相对 SQLite 路径固定到 backend 目录，避免启动目录不同造成空库。"""

    path = Path(value)
    if path.is_absolute():
        return path
    return USER_DATA_ROOT / path


DB_PATH = _db_path()


def _connect() -> sqlite3.Connection:
    ensure_runtime_directories()
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_column(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    definition: str,
) -> None:
    """Add a SQLite column when upgrading an existing development database."""
    columns = {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _drop_column_if_exists(conn: sqlite3.Connection, table: str, column: str) -> None:
    """删除已下线设计遗留列，避免旧 NOT NULL 约束继续影响当前写入。"""

    columns = {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column in columns:
        conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")


def _bind_workspace_resources(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    workspace_id: str,
    now: str,
) -> None:
    """把 Workspace 当前持久资源作为 Project 的初始白名单快照。"""

    for resource_type, table in _PROJECT_RESOURCE_TABLES.items():
        conn.execute(
            f"""
            INSERT OR IGNORE INTO project_resource_bindings (
                project_id, resource_type, resource_id, enabled,
                config_override, created_at, updated_at
            )
            SELECT ?, ?, id, 1, '{{}}', ?, ?
            FROM {table}
            WHERE workspace_id = ?
            """,
            (project_id, resource_type, now, now, workspace_id),
        )
    if workspace_id == DEFAULT_WORKSPACE_ID:
        conn.execute(
            """
            INSERT OR IGNORE INTO project_resource_bindings (
                project_id, resource_type, resource_id, enabled,
                config_override, created_at, updated_at
            )
            SELECT ?, 'http_tool', key, 1, '{}', ?, ?
            FROM json_store
            WHERE namespace = 'knowledge_tools'
            """,
            (project_id, now, now),
        )


def _migrate_project_resource_bindings(conn: sqlite3.Connection, *, now: str) -> None:
    """为升级前已有 Project 建立一次性资源白名单基线。"""

    marker = conn.execute(
        "SELECT 1 FROM json_store WHERE namespace = 'schema_migrations' AND key = ?",
        (_RESOURCE_BINDING_MIGRATION_KEY,),
    ).fetchone()
    if marker is not None:
        return
    for project in conn.execute("SELECT id, workspace_id FROM projects").fetchall():
        _bind_workspace_resources(
            conn,
            project_id=str(project["id"]),
            workspace_id=str(project["workspace_id"]),
            now=now,
        )
    conn.execute(
        """
        INSERT INTO json_store (namespace, key, value, created_at, updated_at)
        VALUES ('schema_migrations', ?, '{"completed":true}', ?, ?)
        """,
        (_RESOURCE_BINDING_MIGRATION_KEY, now, now),
    )


def ensure_db() -> None:
    """Create SQLite tables once, safe to call from import-time route loaders."""
    global _initialized
    if _initialized:
        return

    with _lock:
        if _initialized:
            return
        with _connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS json_store (
                    namespace TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (namespace, key)
                )
            """)
            conn.executescript(BASE_SCHEMA_SQL)
            conn.execute("DROP INDEX IF EXISTS idx_group_studios_active_chat")
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_group_studios_active_chat
                    ON group_studios(chat_session_id)
                    WHERE status NOT IN ('archived', 'failed', 'deleted')
                """
            )
            _ensure_column(conn, "group_messages", "thinking", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(
                conn,
                "group_studios",
                "pending_speaker_member_id",
                "TEXT NOT NULL DEFAULT ''",
            )
            _ensure_column(
                conn,
                "group_studios",
                "awaiting_intake_confirm",
                "INTEGER NOT NULL DEFAULT 0",
            )
            _ensure_column(
                conn,
                "group_studios",
                "speaker_queue_json",
                "TEXT NOT NULL DEFAULT '[]'",
            )
            _ensure_column(
                conn,
                "group_studios",
                "claim_round_json",
                "TEXT NOT NULL DEFAULT '[]'",
            )
            _ensure_column(conn, "chat_messages", "tool_calls", "TEXT NOT NULL DEFAULT '[]'")
            _ensure_column(conn, "chat_messages", "thinking", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(conn, "chat_messages", "model_caches", "TEXT NOT NULL DEFAULT '[]'")
            _ensure_column(conn, "chat_messages", "runtime_events", "TEXT NOT NULL DEFAULT '[]'")
            _ensure_column(conn, "chat_messages", "plan_runs", "TEXT NOT NULL DEFAULT '[]'")
            _ensure_column(
                conn,
                "chat_messages",
                "display_snapshot",
                "TEXT NOT NULL DEFAULT '{\"version\":4,\"lastSeq\":0,\"streamDone\":false,\"order\":[],\"blocksById\":{}}'",
            )
            _ensure_column(conn, "chat_sessions", "mode", "TEXT NOT NULL DEFAULT 'agent'")
            _ensure_column(conn, "chat_sessions", "model_config_id", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(conn, "agents", "workspace_id", "TEXT NOT NULL DEFAULT 'default'")
            _ensure_column(conn, "agents", "role", "TEXT NOT NULL DEFAULT 'chat'")
            _ensure_column(conn, "agents", "job_toolkit", "TEXT NOT NULL DEFAULT '{}'")
            _ensure_column(conn, "mcp_servers", "workspace_id", "TEXT NOT NULL DEFAULT 'default'")
            _ensure_column(conn, "http_tools", "workspace_id", "TEXT NOT NULL DEFAULT 'default'")
            _ensure_column(conn, "skills", "workspace_id", "TEXT NOT NULL DEFAULT 'default'")
            _ensure_column(conn, "chat_sessions", "workspace_id", "TEXT NOT NULL DEFAULT 'default'")
            _ensure_column(conn, "chat_sessions", "project_id", "TEXT NOT NULL DEFAULT 'default'")
            _ensure_column(conn, "chat_runs", "workspace_id", "TEXT NOT NULL DEFAULT 'default'")
            _ensure_column(conn, "chat_runs", "project_id", "TEXT NOT NULL DEFAULT 'default'")
            _ensure_column(conn, "plans", "current_revision", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(conn, "plans", "approved_revision", "INTEGER")
            _ensure_column(conn, "plans", "review_feedback", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(conn, "plans", "execution_run_id", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(conn, "run_events", "workspace_id", "TEXT NOT NULL DEFAULT 'default'")
            _ensure_column(conn, "run_events", "project_id", "TEXT NOT NULL DEFAULT 'default'")
            _ensure_column(conn, "memory_nodes", "workspace_id", "TEXT NOT NULL DEFAULT 'default'")
            _ensure_column(conn, "memory_nodes", "project_id", "TEXT NOT NULL DEFAULT 'default'")
            _ensure_column(conn, "subagent_invocations", "workspace_id", "TEXT NOT NULL DEFAULT 'default'")
            _ensure_column(conn, "subagent_invocations", "project_id", "TEXT NOT NULL DEFAULT 'default'")
            _ensure_column(conn, "subagent_invocations", "execution_mode", "TEXT NOT NULL DEFAULT 'foreground'")
            _ensure_column(conn, "subagent_invocations", "resume_capability", "TEXT NOT NULL DEFAULT 'same_process'")
            _ensure_column(conn, "subagent_invocations", "process_instance_id", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(conn, "subagent_invocations", "result_ref", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(conn, "subagent_invocations", "delivery_status", "TEXT NOT NULL DEFAULT 'none'")
            _ensure_column(conn, "scheduled_jobs", "capability_contract", "TEXT NOT NULL DEFAULT '{}'")
            _drop_column_if_exists(conn, "agents", "callable_agents")
            _drop_column_if_exists(conn, "agents", "max_depth")
            _drop_column_if_exists(conn, "chat_messages", "subagent_calls")
            _drop_column_if_exists(conn, "chat_messages", "team_activities")
            _drop_column_if_exists(conn, "chat_messages", "team_snapshot")
            _drop_column_if_exists(conn, "chat_sessions", "team_id")
            _drop_column_if_exists(conn, "chat_sessions", "team_name")
            _drop_column_if_exists(conn, "plans", "team_id")
            _drop_column_if_exists(conn, "run_events", "team_activity_id")
            conn.execute("DROP TABLE IF EXISTS team_members")
            conn.execute("DROP TABLE IF EXISTS teams")
            conn.execute("DELETE FROM json_store WHERE namespace = 'team_templates'")
            _reset_legacy_chat_stream_data(conn)
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(agents)").fetchall()
            }
            if "plan_config" not in columns:
                conn.execute("ALTER TABLE agents ADD COLUMN plan_config TEXT NOT NULL DEFAULT '{}'")
                conn.commit()
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(agents)").fetchall()
            }
            if "knowledge_tools" not in columns:
                conn.execute("ALTER TABLE agents ADD COLUMN knowledge_tools TEXT NOT NULL DEFAULT '[]'")
                conn.commit()
            _ensure_column(conn, "chat_runs", "updated_at", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(conn, "chat_runs", "model_config_json", "TEXT NOT NULL DEFAULT '{}'")
            _ensure_column(conn, "chat_runs", "trigger_source", "TEXT NOT NULL DEFAULT 'user'")
            _ensure_column(conn, "chat_runs", "scheduled_job_id", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(conn, "chat_runs", "scheduled_firing_id", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(conn, "chat_sessions", "kind", "TEXT NOT NULL DEFAULT 'chat'")
            _ensure_column(conn, "scheduled_jobs", "model_config_id", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(conn, "group_studios", "model_config_id", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(conn, "group_work_items", "review_status", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(conn, "group_work_items", "review_note", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(conn, "group_work_items", "rework_count", "INTEGER NOT NULL DEFAULT 0")
            _drop_column_if_exists(conn, "chat_runs", "team_id")
            _drop_column_if_exists(conn, "chat_runs", "team_name")
            now = _utc_now()
            conn.execute(
                """
                INSERT OR IGNORE INTO workspaces (
                    id, name, description, status, created_at, updated_at
                ) VALUES (?, ?, ?, 'active', ?, ?)
                """,
                (
                    DEFAULT_WORKSPACE_ID,
                    "默认工作区",
                    "历史数据和未显式指定归属的资源所在工作区。",
                    now,
                    now,
                ),
            )
            if USING_CUSTOM_DATA_ROOT:
                default_project = conn.execute(
                    "SELECT root_path FROM projects WHERE id = ?",
                    (DEFAULT_PROJECT_ID,),
                ).fetchone()
                current_root = str(default_project["root_path"] or "") if default_project else ""
                if _is_stale_desktop_default_project_path(current_root):
                    conn.execute(
                        "UPDATE projects SET root_path = ?, updated_at = ? WHERE id = ?",
                        (str(PROJECT_ROOT), now, DEFAULT_PROJECT_ID),
                    )
            conn.execute(
                """
                INSERT OR IGNORE INTO projects (
                    id, workspace_id, name, description, root_path,
                    instructions_file, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, '', 'active', ?, ?)
                """,
                (
                    DEFAULT_PROJECT_ID,
                    DEFAULT_WORKSPACE_ID,
                    "默认项目",
                    "历史会话和未显式选择项目的运行所在项目。",
                    str(PROJECT_ROOT),
                    now,
                    now,
                ),
            )
            for table in ("agents", "mcp_servers", "http_tools", "skills"):
                conn.execute(
                    f"UPDATE {table} SET workspace_id = ? WHERE workspace_id IS NULL OR workspace_id = ''",
                    (DEFAULT_WORKSPACE_ID,),
                )
            for table in ("chat_sessions", "chat_runs", "run_events", "memory_nodes", "subagent_invocations"):
                conn.execute(
                    f"UPDATE {table} SET workspace_id = ? WHERE workspace_id IS NULL OR workspace_id = ''",
                    (DEFAULT_WORKSPACE_ID,),
                )
                conn.execute(
                    f"UPDATE {table} SET project_id = ? WHERE project_id IS NULL OR project_id = ''",
                    (DEFAULT_PROJECT_ID,),
                )
            conn.execute(
                "UPDATE skill_evolution_records SET project_id = ? WHERE project_id IS NULL OR project_id = ''",
                (DEFAULT_PROJECT_ID,),
            )
            _migrate_project_resource_bindings(conn, now=now)
            conn.executescript("""
                CREATE INDEX IF NOT EXISTS idx_chat_sessions_project_updated
                    ON chat_sessions(project_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_chat_runs_project_updated
                    ON chat_runs(project_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_run_events_project_session_order
                    ON run_events(project_id, session_id, sort_order, created_at);
                CREATE INDEX IF NOT EXISTS idx_memory_nodes_project_date
                    ON memory_nodes(project_id, node_date DESC);
                CREATE INDEX IF NOT EXISTS idx_subagent_invocations_project_run
                    ON subagent_invocations(project_id, run_id, started_at);
            """)
            conn.commit()
            _initialized = True


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def _reset_legacy_chat_stream_data(conn: sqlite3.Connection) -> None:
    """检测到旧 RunState/V3 Snapshot 时一次性清空 Chat 运行域，不做数据迁移。"""

    table_names = {
        str(row["name"])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    }
    has_legacy_tables = bool({"chat_run_events", "chat_run_states"} & table_names)
    has_legacy_snapshot = False
    for row in conn.execute("SELECT display_snapshot FROM chat_messages").fetchall():
        snapshot = _loads(row["display_snapshot"], {})
        if not isinstance(snapshot, dict) or snapshot.get("version") != 4:
            has_legacy_snapshot = True
            break
    if has_legacy_tables or has_legacy_snapshot:
        for table in (
            "projection_frames",
            "projection_streams",
            "chat_domain_events",
            "chat_domain_streams",
            "subagent_transcript_items",
            "subagent_invocations",
            "plan_events",
            "plan_steps",
            "plans",
            "run_events",
            "chat_messages",
            "chat_runs",
            "chat_sessions",
        ):
            if table in table_names:
                conn.execute(f"DELETE FROM {table}")
    conn.execute("DROP TABLE IF EXISTS chat_run_events")
    conn.execute("DROP TABLE IF EXISTS chat_run_states")


def _workspace_project_record_store() -> WorkspaceProjectRecordStore:
    """创建复用当前数据库基础设施的 Workspace / Project 领域存储。"""

    return WorkspaceProjectRecordStore(
        ensure_db=ensure_db,
        connect=_connect,
        lock=_lock,
        utc_now=_utc_now,
        dumps=_dumps,
        loads=_loads,
        bind_workspace_resources=_bind_workspace_resources,
        resource_tables=_PROJECT_RESOURCE_TABLES,
        default_workspace_id=DEFAULT_WORKSPACE_ID,
        project_root=PROJECT_ROOT,
    )


def list_workspaces(*, include_archived: bool = False) -> list[dict[str, Any]]:
    """列出 Workspace。"""

    return _workspace_project_record_store().list_workspaces(include_archived=include_archived)


def get_workspace(workspace_id: str) -> dict[str, Any] | None:
    """读取一个 Workspace。"""

    return _workspace_project_record_store().get_workspace(workspace_id)


def create_workspace(data: dict[str, Any]) -> dict[str, Any]:
    """创建 Workspace。"""

    return _workspace_project_record_store().create_workspace(data)


def list_projects(
    workspace_id: str | None = None,
    *,
    include_archived: bool = False,
) -> list[dict[str, Any]]:
    """列出 Project，可按 Workspace 过滤。"""

    return _workspace_project_record_store().list_projects(
        workspace_id,
        include_archived=include_archived,
    )


def get_project(project_id: str) -> dict[str, Any] | None:
    """读取一个 Project。"""

    return _workspace_project_record_store().get_project(project_id)


def create_project(data: dict[str, Any]) -> dict[str, Any]:
    """创建 Project。"""

    return _workspace_project_record_store().create_project(data)


def update_project(project_id: str, changes: dict[str, Any]) -> dict[str, Any] | None:
    """更新 Project 可编辑字段。"""

    return _workspace_project_record_store().update_project(project_id, changes)


def list_project_resource_bindings(project_id: str) -> list[dict[str, Any]]:
    """列出 Project 资源绑定。"""

    return _workspace_project_record_store().list_resource_bindings(project_id)


def get_workspace_resource(
    resource_type: str,
    resource_id: str,
) -> dict[str, str] | None:
    """读取持久资源的 Workspace 归属。"""

    if resource_type == "agent":
        try:
            from src.catalog.loader import get_registered_agent

            agent = get_registered_agent(resource_id)
            if agent is not None:
                return {"id": agent.id, "workspaceId": agent.workspace_id}
        except Exception:
            pass
    found = _workspace_project_record_store().get_workspace_resource(resource_type, resource_id)
    if found is not None:
        return found
    if resource_type == "mcp":
        from src.catalog.mcp_files import discover_mcp_files

        for record in discover_mcp_files():
            if record.id == resource_id:
                return {"id": record.id, "workspaceId": record.workspace_id}
    if resource_type == "http_tool":
        from src.catalog.http_files import discover_http_files

        for record in discover_http_files():
            if record.id == resource_id:
                return {"id": record.id, "workspaceId": record.workspace_id}
    return None


def upsert_project_resource_binding(data: dict[str, Any]) -> dict[str, Any]:
    """创建或更新一个 Project 资源绑定。"""

    return _workspace_project_record_store().upsert_resource_binding(data)


def get_chat_session_ownership(session_id: str) -> dict[str, str] | None:
    """读取 Session 的 Workspace / Project 归属。"""

    ensure_db()
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT workspace_id, project_id FROM chat_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
    if row is None:
        return None
    return {
        "workspaceId": str(row["workspace_id"] or DEFAULT_WORKSPACE_ID),
        "projectId": str(row["project_id"] or DEFAULT_PROJECT_ID),
    }


def _json_document_store() -> JsonDocumentStore:
    """创建复用当前数据库基础设施的 JSON Namespace 存储。"""

    return JsonDocumentStore(
        ensure_db=ensure_db,
        connect=_connect,
        lock=_lock,
        utc_now=_utc_now,
    )


def list_json_records(namespace: str) -> list[dict[str, Any]]:
    """List all JSON documents in a namespace."""

    return _json_document_store().list(namespace)


def get_json_record(namespace: str, key: str) -> dict[str, Any] | None:
    """Read one JSON document."""
    return _json_document_store().get(namespace, key)


def upsert_json_record(namespace: str, key: str, value: dict[str, Any]) -> None:
    """Insert or update one JSON document."""
    _json_document_store().upsert(namespace, key, value)


def replace_json_namespace(namespace: str, records: dict[str, dict[str, Any]]) -> None:
    """Replace all records in one namespace atomically."""
    _json_document_store().replace_namespace(namespace, records)


def delete_json_record(namespace: str, key: str) -> None:
    """Delete one JSON document."""
    _json_document_store().delete(namespace, key)


def namespace_has_records(namespace: str) -> bool:
    """Return whether a namespace contains records."""
    return _json_document_store().has_records(namespace)


def migrate_json_list_file(
    namespace: str,
    path: str | Path,
    key_field: str = "id",
) -> None:
    """Import an existing JSON list file into SQLite if namespace is empty."""
    _json_document_store().migrate_list_file(namespace, path, key_field)


def migrate_json_object_file(
    namespace: str,
    key: str,
    path: str | Path,
) -> None:
    """Import an existing JSON object file into SQLite if the record is empty."""
    _json_document_store().migrate_object_file(namespace, key, path)


def _resource_catalog_record_store() -> ResourceCatalogRecordStore:
    """创建复用当前数据库基础设施的持久资源目录存储。"""

    return ResourceCatalogRecordStore(
        ensure_db=ensure_db,
        connect=_connect,
        lock=_lock,
        utc_now=_utc_now,
        dumps=_dumps,
        loads=_loads,
        normalize_guardrails=_normalize_guardrails,
        default_model_config=DEFAULT_MODEL_CONFIG,
        default_plan_config=DEFAULT_PLAN_CONFIG,
        default_guardrails=DEFAULT_GUARDRAILS,
        default_workspace_id=DEFAULT_WORKSPACE_ID,
    )


def list_agents() -> list[dict[str, Any]]:
    return _resource_catalog_record_store().list_agents()


def upsert_agent(data: dict[str, Any]) -> None:
    """迁移完成后禁止再把用户正文写入 sqlite `agents` 表。"""

    migrated = False
    try:
        from src.catalog.migrate_agents import is_agent_catalog_migrated

        migrated = is_agent_catalog_migrated()
    except Exception:
        migrated = False
    if migrated:
        # 规格：marker=done 后 sqlite 不是 Agent 正文权威。测试夹具仍可走 catalog 文件。
        pass
    else:
        _resource_catalog_record_store().upsert_agent(data)
    from src.catalog.agent_models import AgentResponse
    from src.catalog.system_defaults import utc_now
    from src.server.routes import agents as agents_routes

    payload = {
        "id": data["id"],
        "name": data.get("name") or data["id"],
        "avatar": data.get("avatar", ""),
        "description": data.get("description", ""),
        "system_prompt": data.get("system_prompt") or "prompt",
        "modelConfig": data.get("modelConfig") or {},
        "mcpTools": data.get("mcpTools") or data.get("mcp_tools") or [],
        "knowledgeTools": data.get("knowledgeTools") or data.get("knowledge_tools") or [],
        "skills": data.get("skills") or [],
        "tags": data.get("tags") or [],
        "guardrails": data.get("guardrails") or {},
        "status": "idle",
        "created_at": data.get("created_at") or utc_now(),
        "updated_at": data.get("updated_at") or utc_now(),
        "workspaceId": data.get("workspaceId") or data.get("workspace_id") or "default",
        "role": data.get("role") or "chat",
        "jobToolkit": data.get("jobToolkit") or data.get("job_toolkit") or {},
    }
    record = AgentResponse.model_validate(payload)
    agents_routes._agents[record.id] = record
    try:
        from src.catalog.loader import import_sqlite_agent_record

        import_sqlite_agent_record(data)
    except Exception:
        pass


def replace_agents(records: dict[str, dict[str, Any]]) -> None:
    _resource_catalog_record_store().replace_agents(records)


def delete_agent(agent_id: str) -> None:
    _resource_catalog_record_store().delete_agent(agent_id)
    try:
        from src.catalog.loader import delete_agent as catalog_delete_agent
        from src.server.routes import agents as agents_routes

        agents_routes._agents.pop(agent_id, None)
        try:
            catalog_delete_agent(agent_id)
        except (FileNotFoundError, PermissionError):
            pass
    except Exception:
        pass


def list_mcp_servers() -> list[dict[str, Any]]:
    return _resource_catalog_record_store().list_mcp_servers()


def replace_mcp_servers(records: dict[str, dict[str, Any]]) -> None:
    _resource_catalog_record_store().replace_mcp_servers(records)


def list_http_tools() -> list[dict[str, Any]]:
    """List configurable HTTP tools."""
    return _resource_catalog_record_store().list_http_tools()


def upsert_http_tool(data: dict[str, Any]) -> dict[str, Any]:
    """Insert or update one configurable HTTP tool."""
    return _resource_catalog_record_store().upsert_http_tool(data)


def delete_http_tool(tool_id: str) -> bool:
    """Delete one configurable HTTP tool."""
    return _resource_catalog_record_store().delete_http_tool(tool_id)


def list_skills() -> list[dict[str, Any]]:
    return _resource_catalog_record_store().list_skills()


def replace_skills(records: dict[str, dict[str, Any]]) -> None:
    _resource_catalog_record_store().replace_skills(records)


def _skill_run_record_store() -> SkillRunRecordStore:
    """创建复用当前数据库基础设施的 Skill Run 存储。"""

    return SkillRunRecordStore(
        ensure_db=ensure_db,
        connect=_connect,
        lock=_lock,
        utc_now=_utc_now,
        dumps=_dumps,
        loads=_loads,
    )


def get_active_skill_run(session_id: str, agent_id: str) -> dict[str, Any] | None:
    return _skill_run_record_store().get_active(session_id, agent_id)


def list_recent_skill_run_names(session_id: str, agent_id: str, *, limit: int = 5) -> list[str]:
    return _skill_run_record_store().list_recent_skill_names(session_id, agent_id, limit=limit)


def get_skill_run_by_pending_interaction(interaction_id: str) -> dict[str, Any] | None:
    return _skill_run_record_store().get_by_pending_interaction(interaction_id)


def _memory_markdown_candidate_store() -> MemoryMarkdownCandidateStore:
    """创建复用当前数据库基础设施的 Markdown 记忆候选存储。"""

    return MemoryMarkdownCandidateStore(
        ensure_db=ensure_db,
        connect=_connect,
        lock=_lock,
        utc_now=_utc_now,
    )


def create_memory_markdown_candidate(payload: dict[str, Any]) -> dict[str, Any]:
    return _memory_markdown_candidate_store().create(payload)


def find_pending_memory_markdown_candidate(*, scope: str, owner_id: str, note: str, exact_text: str) -> dict[str, Any] | None:
    return _memory_markdown_candidate_store().find_pending_duplicate(
        scope=scope, owner_id=owner_id, note=note, exact_text=exact_text
    )


def get_memory_markdown_candidate(record_id: str) -> dict[str, Any] | None:
    return _memory_markdown_candidate_store().get(record_id)


def list_memory_markdown_candidates(
    *,
    status: str | None = "pending",
    scope: str | None = None,
    project_id: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    return _memory_markdown_candidate_store().list(status=status, scope=scope, project_id=project_id, limit=limit)


def count_pending_memory_markdown_candidates() -> int:
    return _memory_markdown_candidate_store().count_pending()


def set_memory_markdown_candidate_status(record_id: str, *, status: str, resolution: str = "") -> dict[str, Any] | None:
    return _memory_markdown_candidate_store().set_status(record_id, status=status, resolution=resolution)


def _session_context_state_store() -> SessionContextStateStore:
    """创建复用当前数据库基础设施的会话上下文状态存储。"""

    return SessionContextStateStore(
        ensure_db=ensure_db,
        connect=_connect,
        lock=_lock,
        utc_now=_utc_now,
    )


def save_session_context_state(
    *,
    session_id: str,
    agent_id: str,
    summary: str,
    middle_context: dict[str, Any] | None,
) -> None:
    _session_context_state_store().save(
        session_id=session_id,
        agent_id=agent_id,
        summary=summary,
        middle_context=middle_context,
    )


def load_session_context_state(session_id: str) -> dict[str, Any] | None:
    return _session_context_state_store().load(session_id)


def delete_session_context_state(session_id: str) -> None:
    _session_context_state_store().delete(session_id)


def upsert_skill_run(record: dict[str, Any]) -> dict[str, Any]:
    return _skill_run_record_store().upsert(record)


def load_chat_store() -> dict[str, Any]:
    ensure_db()
    with _lock, _connect() as conn:
        sessions = conn.execute(
            "SELECT * FROM chat_sessions ORDER BY updated_at ASC"
        ).fetchall()
        messages = conn.execute(
            "SELECT * FROM chat_messages ORDER BY session_id, sort_order ASC"
        ).fetchall()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in messages:
        grouped.setdefault(row["session_id"], []).append({
            "id": row["id"],
            "role": row["role"],
            "content": row["content"],
            "agent_id": row["agent_id"],
            "agent_name": row["agent_name"],
            "model_name": row["model_name"],
            "error": row["error"],
            "thinking": row["thinking"],
            "images": _loads(row["images"], []),
            "displaySnapshot": _loads(row["display_snapshot"], EMPTY_DISPLAY_SNAPSHOT),
            "created_at": row["created_at"],
        })
    return {
        "sessions": [
            {
                "id": row["id"],
                "title": row["title"],
                "agent_id": row["agent_id"],
                "agent_name": row["agent_name"],
                "mode": row["mode"],
                "workspace_id": row["workspace_id"],
                "project_id": row["project_id"],
                "kind": (row["kind"] if "kind" in row.keys() and row["kind"] else "chat"),
                "modelConfigId": row["model_config_id"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "message_count": row["message_count"],
            }
            for row in sessions
        ],
        "messages": grouped,
    }


def save_chat_store(data: dict[str, Any]) -> None:
    ensure_db()
    sessions = data.get("sessions", [])
    messages_by_session = data.get("messages", {})
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM chat_messages")
        conn.execute("DELETE FROM chat_sessions")
        conn.executemany(
            """
            INSERT INTO chat_sessions (
                id, title, agent_id, agent_name, mode, workspace_id, project_id, kind,
                created_at, updated_at, message_count, model_config_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    item["id"], item["title"], item["agent_id"], item["agent_name"],
                    item.get("mode", "agent"),
                    item.get("workspace_id", item.get("workspaceId", DEFAULT_WORKSPACE_ID)),
                    item.get("project_id", item.get("projectId", DEFAULT_PROJECT_ID)),
                    item.get("kind") or "chat",
                    item["created_at"],
                    item["updated_at"], item.get("message_count", 0),
                    item.get("modelConfigId", item.get("model_config_id", "")),
                )
                for item in sessions
            ],
        )
        rows = []
        for session_id, messages in messages_by_session.items():
            for index, item in enumerate(messages):
                rows.append((
                    item["id"], session_id, item["role"], item.get("content", ""),
                    item.get("agent_id"), item.get("agent_name"), item.get("model_name"),
                    item.get("error"), item.get("thinking", ""), _dumps(item.get("images", [])),
                    _dumps([]),
                    _dumps([]),
                    _dumps([]),
                    _dumps([]),
                    _dumps([]),
                    _dumps(item.get("displaySnapshot", item.get("display_snapshot", EMPTY_DISPLAY_SNAPSHOT))),
                    item["created_at"], index,
                ))
        conn.executemany(
            """
            INSERT INTO chat_messages (
                id, session_id, role, content, agent_id, agent_name, model_name,
                error, thinking, images, tool_calls, skill_calls,
                model_caches, runtime_events, plan_runs,
                display_snapshot, created_at,
                sort_order
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()


def _trace_record_store() -> TraceRecordStore:
    """创建复用当前数据库基础设施的运行轨迹审计存储。"""

    return TraceRecordStore(
        ensure_db=ensure_db,
        connect=_connect,
        lock=_lock,
        utc_now=_utc_now,
        dumps=_dumps,
        loads=_loads,
        default_workspace_id=DEFAULT_WORKSPACE_ID,
        default_project_id=DEFAULT_PROJECT_ID,
    )


def append_run_event(
    *,
    session_id: str,
    event_type: str,
    scope: str = "leader",
    agent_id: str | None = None,
    agent_name: str | None = None,
    status: str = "",
    title: str = "",
    content: str = "",
    payload: dict[str, Any] | None = None,
    workspace_id: str | None = None,
    project_id: str | None = None,
) -> dict[str, Any]:
    """Append one normalized runtime event for trace inspection."""
    return _trace_record_store().append_event(
        session_id=session_id,
        event_type=event_type,
        scope=scope,
        agent_id=agent_id,
        agent_name=agent_name,
        status=status,
        title=title,
        content=content,
        payload=payload,
        workspace_id=workspace_id,
        project_id=project_id,
    )


def list_run_events(
    session_id: str | None = None,
    *,
    include_payload: bool = True,
    limit: int | None = None,
    offset: int = 0,
    latest: bool = False,
) -> list[dict[str, Any]]:
    """List normalized runtime events, optionally scoped to one session."""
    return _trace_record_store().list_events(
        session_id,
        include_payload=include_payload,
        limit=limit,
        offset=offset,
        latest=latest,
    )


def create_subagent_invocation(data: dict[str, Any]) -> dict[str, Any]:
    """创建 Subagent invocation 审计记录。"""

    return _trace_record_store().create_invocation(data)


def update_subagent_invocation(
    invocation_id: str,
    *,
    status: str,
    result_preview: str = "",
    error: str = "",
    completed_at: str | None = None,
) -> dict[str, Any] | None:
    """更新 Subagent invocation 审计记录。"""

    return _trace_record_store().update_invocation(
        invocation_id,
        status=status,
        result_preview=result_preview,
        error=error,
        completed_at=completed_at,
    )


def update_subagent_invocation_delivery(
    invocation_id: str,
    *,
    delivery_status: str,
    result_ref: str = "",
) -> dict[str, Any] | None:
    """更新 Subagent 结果引用和父 Agent消费状态索引。"""

    return _trace_record_store().update_invocation_delivery(
        invocation_id,
        delivery_status=delivery_status,
        result_ref=result_ref,
    )


def list_subagent_invocations(
    *,
    run_id: str | None = None,
    session_id: str | None = None,
    project_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """列出 Subagent invocation 审计记录。"""

    return _trace_record_store().list_invocations(
        run_id=run_id,
        session_id=session_id,
        project_id=project_id,
        limit=limit,
    )


def get_subagent_invocation(invocation_id: str) -> dict[str, Any] | None:
    """按 invocation id 获取 Subagent 持久任务。"""

    return _trace_record_store().get_invocation(invocation_id)


def reconcile_interrupted_subagent_invocations() -> int:
    """启动期收口上个后端进程遗留的 running Subagent task。"""

    return _trace_record_store().reconcile_interrupted_invocations()


def append_subagent_transcript_item(
    *,
    invocation_id: str,
    kind: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """追加 Subagent invocation 的 transcript 条目。"""

    return _trace_record_store().append_transcript_item(
        invocation_id=invocation_id,
        kind=kind,
        payload=payload,
    )


def list_subagent_transcript_items(invocation_id: str, *, limit: int = 500) -> list[dict[str, Any]]:
    """列出 Subagent invocation transcript。"""

    return _trace_record_store().list_transcript_items(invocation_id, limit=limit)


def list_run_events_by_type(
    event_type: str,
    *,
    project_id: str = DEFAULT_PROJECT_ID,
    include_payload: bool = True,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """按事件类型列出运行事件，用于 Memory 审计等横向视图。"""

    return _trace_record_store().list_events_by_type(
        event_type,
        project_id=project_id,
        include_payload=include_payload,
        limit=limit,
        offset=offset,
    )


def count_run_events(session_id: str) -> int:
    """Count normalized runtime events for one session."""
    return _trace_record_store().count_events(session_id)


def _chat_run_record_store() -> ChatRunRecordStore:
    """创建复用当前数据库连接与锁的 Chat Run 领域存储。"""

    return ChatRunRecordStore(
        ensure_db=ensure_db,
        connect=_connect,
        lock=_lock,
        utc_now=_utc_now,
        default_workspace_id=DEFAULT_WORKSPACE_ID,
        default_project_id=DEFAULT_PROJECT_ID,
    )


def upsert_chat_run(record: dict[str, Any]) -> dict[str, Any]:
    """新增或更新一个持久化 Chat Run。"""

    return _chat_run_record_store().upsert(record)


def update_chat_run_status(run_id: str, status: str, completed_at: str | None = None) -> None:
    """更新持久化 Chat Run 状态。"""

    _chat_run_record_store().update_status(run_id, status, completed_at)


def list_chat_runs(
    limit: int = 50,
    *,
    project_id: str | None = None,
    trigger_source: str | None = None,
) -> list[dict[str, Any]]:
    """列出持久化 Chat Run。"""

    return _chat_run_record_store().list(
        limit,
        project_id=project_id,
        trigger_source=trigger_source,
    )


def list_chat_runs_by_statuses(statuses: list[str] | tuple[str, ...] | set[str]) -> list[dict[str, Any]]:
    """按状态列出全部 Chat Run，不受默认 LIMIT 截断。"""

    return _chat_run_record_store().list_by_statuses(statuses)


def get_chat_run(run_id: str) -> dict[str, Any] | None:
    """读取一个持久化 Chat Run。"""

    return _chat_run_record_store().get(run_id)


def _workflow_record_store() -> WorkflowRecordStore:
    """创建复用当前数据库连接与锁的 Workflow 领域存储。"""

    return WorkflowRecordStore(
        ensure_db=ensure_db,
        connect=_connect,
        lock=_lock,
        utc_now=_utc_now,
        dumps=_dumps,
        loads=_loads,
    )


def create_workflow_run(record: dict[str, Any]) -> dict[str, Any]:
    """创建 Dynamic Workflow Run 及初始节点和边。"""

    return _workflow_record_store().create(record)


def list_workflow_runs(limit: int = 50) -> list[dict[str, Any]]:
    """列出最近的 Dynamic Workflow Run。"""

    return _workflow_record_store().list(limit)


def get_workflow_run(run_id: str, *, event_limit: int | None = None) -> dict[str, Any] | None:
    """读取 Dynamic Workflow Run 完整快照。"""

    return _workflow_record_store().get(run_id, event_limit=event_limit)


def _group_studio_record_store() -> GroupStudioRecordStore:
    """创建复用当前数据库连接与锁的群聊工作室存储。"""

    return GroupStudioRecordStore(
        ensure_db=ensure_db,
        connect=_connect,
        lock=_lock,
        utc_now=_utc_now,
        dumps=_dumps,
        loads=_loads,
    )


def create_group_studio_from_chat(payload: dict[str, Any]) -> dict[str, Any]:
    """创建或返回当前 Chat 会话已有的未归档工作室。"""

    return _group_studio_record_store().create_from_chat(payload)


def get_group_studio_snapshot(group_id: str, *, message_limit: int = 100) -> dict[str, Any] | None:
    """读取工作室 GET /{id} 快照。"""

    return _group_studio_record_store().get_snapshot(group_id, message_limit=message_limit)


def get_active_group_studio_by_chat_session(chat_session_id: str) -> dict[str, Any] | None:
    """按 Chat session 读取未归档工作室。"""

    return _group_studio_record_store().get_active_by_chat_session(chat_session_id)


def lookup_active_group_studios_by_chat_session_ids(
    session_ids: list[str],
) -> dict[str, dict[str, str]]:
    """catalog 查询：session_id -> 活跃工作室摘要。"""

    return _group_studio_record_store().lookup_active_by_chat_session_ids(session_ids)


def list_group_studios_by_project(project_id: str) -> list[dict[str, Any]]:
    """列出某 Project 工作室（含已归档，不含用户删除）。"""

    return _group_studio_record_store().list_active_by_project(project_id)


def find_blank_group_studio_draft_id(project_id: str) -> str | None:
    return _group_studio_record_store().find_blank_draft_id(project_id)


def list_group_studios_with_current_run() -> list[dict[str, Any]]:
    return _group_studio_record_store().list_with_current_run()


def list_group_studio_messages(
    group_id: str,
    *,
    before: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """分页读取群消息。"""

    return _group_studio_record_store().list_messages(group_id, before=before, limit=limit)


def archive_group_studio(group_id: str, *, status: str = "archived") -> None:
    """把工作室标为 archived（结项保留）或 deleted（列表隐藏）。"""

    _group_studio_record_store().archive(group_id, status=status)


def group_studio_has_runs(group_id: str) -> bool:
    """工作室是否已经产生过 GroupRun。"""

    return _group_studio_record_store().has_runs(group_id)


def count_group_studio_active_members(group_id: str) -> int:
    return _group_studio_record_store().count_active_members(group_id)


def find_group_studio_member_by_agent(group_id: str, agent_id: str) -> dict[str, Any] | None:
    return _group_studio_record_store().find_active_member_by_agent(group_id, agent_id)


def insert_group_studio_member(group_id: str, member: dict[str, Any]) -> dict[str, Any]:
    return _group_studio_record_store().insert_member(group_id, member)


def apply_group_studio_task_card_update(
    group_id: str,
    *,
    goal: str,
    background: str,
    constraints: list[str],
    dod_checklist: list[dict[str, Any]],
    card_status: str,
    studio_status: str,
    awaiting_restatement: bool,
    phase_before_pause: str | None,
    bump_revision: bool,
    title: str | None = None,
) -> dict[str, Any]:
    return _group_studio_record_store().apply_task_card_update(
        group_id,
        goal=goal,
        background=background,
        constraints=constraints,
        dod_checklist=dod_checklist,
        card_status=card_status,
        studio_status=studio_status,
        awaiting_restatement=awaiting_restatement,
        phase_before_pause=phase_before_pause,
        bump_revision=bump_revision,
        title=title,
    )


def get_group_studio_member(group_id: str, member_id: str) -> dict[str, Any] | None:
    return _group_studio_record_store().get_member(group_id, member_id)


def set_group_studio_member_muted(group_id: str, member_id: str, muted: bool) -> dict[str, Any] | None:
    return _group_studio_record_store().set_member_muted(group_id, member_id, muted)


def set_group_studio_member_removed(group_id: str, member_id: str) -> dict[str, Any] | None:
    return _group_studio_record_store().set_member_removed(group_id, member_id)


def set_group_studio_member_last_spoke(
    group_id: str,
    member_id: str,
    spoke_at: str | None = None,
) -> None:
    _group_studio_record_store().set_member_last_spoke(group_id, member_id, spoke_at)


def set_group_studio_status(group_id: str, status: str) -> None:
    _group_studio_record_store().set_studio_status(group_id, status)


def update_group_studio_state(group_id: str, **changes: Any) -> dict[str, Any]:
    return _group_studio_record_store().update_studio_state(group_id, **changes)


def update_group_studio_model_config(group_id: str, model_config_id: str) -> dict[str, Any]:
    return _group_studio_record_store().update_studio_state(
        group_id, model_config_id=model_config_id
    )


def set_group_studio_current_run_id(group_id: str, run_id: str | None) -> None:
    _group_studio_record_store().set_current_run_id(group_id, run_id)


def set_group_studio_pending_speaker(group_id: str, member_id: str | None) -> None:
    _group_studio_record_store().set_pending_speaker(group_id, member_id)


def get_group_studio_pending_speaker(group_id: str) -> str:
    return _group_studio_record_store().get_pending_speaker(group_id)


def get_group_studio_speaker_queue(group_id: str) -> list[str]:
    return _group_studio_record_store().get_speaker_queue(group_id)


def set_group_studio_speaker_queue(group_id: str, member_ids: list[str]) -> None:
    _group_studio_record_store().set_speaker_queue(group_id, member_ids)


def pop_group_studio_speaker_queue_head(group_id: str, expected: str | None = None) -> str:
    return _group_studio_record_store().pop_speaker_queue_head(group_id, expected)


def get_group_studio_claim_round(group_id: str) -> list[str]:
    return _group_studio_record_store().get_claim_round(group_id)


def set_group_studio_claim_round(group_id: str, member_ids: list[str]) -> None:
    _group_studio_record_store().set_claim_round(group_id, member_ids)


def pop_group_studio_claim_round_head(group_id: str, expected: str | None = None) -> str:
    return _group_studio_record_store().pop_claim_round_head(group_id, expected)


def insert_group_studio_work_item(payload: dict[str, Any]) -> dict[str, Any]:
    return _group_studio_record_store().insert_work_item(payload)


def list_group_studio_work_items(group_id: str) -> list[dict[str, Any]]:
    return _group_studio_record_store().list_work_items(group_id)


def claim_group_studio_work_item(
    group_id: str,
    work_item_id: str,
    *,
    member_id: str,
    run_id: str,
) -> dict[str, Any] | None:
    return _group_studio_record_store().claim_work_item(
        group_id, work_item_id, member_id=member_id, run_id=run_id,
    )


def complete_group_studio_work_item(
    group_id: str,
    work_item_id: str,
    *,
    member_id: str,
) -> dict[str, Any] | None:
    return _group_studio_record_store().complete_work_item(
        group_id, work_item_id, member_id=member_id,
    )


def cancel_group_studio_work_item(group_id: str, work_item_id: str) -> dict[str, Any] | None:
    return _group_studio_record_store().cancel_work_item(group_id, work_item_id)


def review_group_studio_work_item(
    group_id: str,
    work_item_id: str,
    *,
    verdict: str,
    note: str = "",
) -> dict[str, Any] | None:
    return _group_studio_record_store().review_work_item(group_id, work_item_id, verdict=verdict, note=note)


def insert_group_studio_run(payload: dict[str, Any]) -> dict[str, Any]:
    return _group_studio_record_store().insert_run(payload)


def update_group_studio_run_status(run_id: str, status: str, error: str = "") -> None:
    _group_studio_record_store().update_run_status(run_id, status, error)


def insert_group_studio_message(payload: dict[str, Any]) -> dict[str, Any]:
    return _group_studio_record_store().insert_message(payload)


def update_group_studio_message_body(message_id: str, body: str, thinking: str | None = None) -> None:
    _group_studio_record_store().update_message_body(message_id, body, thinking)


def append_group_studio_event(payload: dict[str, Any]) -> dict[str, Any]:
    return _group_studio_record_store().append_event(payload)


def list_group_studio_events_after(group_id: str, after_seq: int) -> list[dict[str, Any]]:
    return _group_studio_record_store().list_events_after(group_id, after_seq)


def replace_group_studio_blackboard(group_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return _group_studio_record_store().replace_blackboard_payload(group_id, payload)


def list_group_studio_artifacts(group_id: str) -> list[dict[str, Any]]:
    return _group_studio_record_store().list_artifacts(group_id)


def insert_group_studio_artifact(payload: dict[str, Any]) -> dict[str, Any]:
    return _group_studio_record_store().insert_artifact(payload)


def insert_group_studio_approval(payload: dict[str, Any]) -> dict[str, Any]:
    return _group_studio_record_store().insert_approval(payload)


def get_group_studio_approval(approval_id: str) -> dict[str, Any] | None:
    return _group_studio_record_store().get_approval(approval_id)


def get_group_studio_pending_approval(group_id: str, run_id: str | None = None) -> dict[str, Any] | None:
    return _group_studio_record_store().get_pending_approval(group_id, run_id)


def update_group_studio_approval_status(approval_id: str, status: str) -> dict[str, Any] | None:
    return _group_studio_record_store().update_approval_status(approval_id, status)


def update_workflow_run_status(
    run_id: str,
    status: str,
    *,
    current_node_id: str | None = None,
    completed_at: str | None = None,
) -> None:
    """更新 Dynamic Workflow Run 状态。"""

    _workflow_record_store().update_run_status(
        run_id,
        status,
        current_node_id=current_node_id,
        completed_at=completed_at,
    )


def update_workflow_node_status(
    node_id: str,
    status: str,
    *,
    summary: str | None = None,
    output: str | None = None,
    error: str | None = None,
    started_at: str | None = None,
    completed_at: str | None = None,
) -> dict[str, Any] | None:
    """更新 Dynamic Workflow 节点状态。"""

    return _workflow_record_store().update_node_status(
        node_id,
        status,
        summary=summary,
        output=output,
        error=error,
        started_at=started_at,
        completed_at=completed_at,
    )


def append_workflow_event(
    run_id: str,
    event_type: str,
    *,
    node_id: str = "",
    status: str = "",
    title: str = "",
    content: str = "",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """追加 Dynamic Workflow 事件。"""

    return _workflow_record_store().append_event(
        run_id,
        event_type,
        node_id=node_id,
        status=status,
        title=title,
        content=content,
        payload=payload,
    )


def get_latest_assistant_chat_message(session_id: str) -> dict[str, Any] | None:
    """读取某个会话里最近一条助手消息。"""

    ensure_db()
    with _lock, _connect() as conn:
        row = conn.execute(
            """
            SELECT * FROM chat_messages
            WHERE session_id = ? AND role = 'assistant'
            ORDER BY sort_order DESC, created_at DESC
            LIMIT 1
            """,
            (session_id,),
        ).fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "sessionId": row["session_id"],
        "role": row["role"],
        "content": row["content"] or "",
    }


def get_latest_user_chat_message(session_id: str, content: str | None = None) -> dict[str, Any] | None:
    """读取某个会话里最近一条用户消息，可按内容精确匹配。"""

    ensure_db()
    with _lock, _connect() as conn:
        if content is None:
            row = conn.execute(
                """
                SELECT * FROM chat_messages
                WHERE session_id = ? AND role = 'user'
                ORDER BY sort_order DESC, created_at DESC
                LIMIT 1
                """,
                (session_id,),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT * FROM chat_messages
                WHERE session_id = ? AND role = 'user' AND content = ?
                ORDER BY sort_order DESC, created_at DESC
                LIMIT 1
                """,
                (session_id, content),
            ).fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "sessionId": row["session_id"],
        "workspaceId": row["workspace_id"],
        "projectId": row["project_id"],
        "role": row["role"],
        "content": row["content"],
        "agentId": row["agent_id"],
        "agentName": row["agent_name"],
        "modelName": row["model_name"],
        "images": _loads(row["images"], []),
        "createdAt": row["created_at"],
    }


def get_latest_assistant_message_preview(session_id: str) -> dict[str, Any] | None:
    """读取某个会话最近一条非空 Assistant 文本，用于轻量结果摘要。"""

    ensure_db()
    with _lock, _connect() as conn:
        row = conn.execute(
            """
            SELECT id, content, agent_name, model_name, created_at
            FROM chat_messages
            WHERE session_id = ?
              AND role = 'assistant'
              AND length(trim(content)) > 0
            ORDER BY sort_order DESC, created_at DESC
            LIMIT 1
            """,
            (session_id,),
        ).fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "content": row["content"],
        "agentName": row["agent_name"],
        "modelName": row["model_name"],
        "createdAt": row["created_at"],
    }


def get_latest_completed_user_assistant_turn(
    session_id: str,
    *,
    before_user_content: str | None = None,
) -> dict[str, Any] | None:
    """读取当前用户消息之前最近一轮完整 user -> assistant 对话。"""

    ensure_db()
    with _lock, _connect() as conn:
        before_sort_order: int | None = None
        if before_user_content is not None:
            current_user = conn.execute(
                """
                SELECT sort_order FROM chat_messages
                WHERE session_id = ? AND role = 'user' AND content = ?
                ORDER BY sort_order DESC, created_at DESC
                LIMIT 1
                """,
                (session_id, before_user_content),
            ).fetchone()
            if current_user:
                before_sort_order = int(current_user["sort_order"])

        if before_sort_order is None:
            assistant_row = conn.execute(
                """
                SELECT * FROM chat_messages
                WHERE session_id = ?
                  AND role = 'assistant'
                  AND length(trim(content)) > 0
                ORDER BY sort_order DESC, created_at DESC
                LIMIT 1
                """,
                (session_id,),
            ).fetchone()
        else:
            assistant_row = conn.execute(
                """
                SELECT * FROM chat_messages
                WHERE session_id = ?
                  AND role = 'assistant'
                  AND length(trim(content)) > 0
                  AND sort_order < ?
                ORDER BY sort_order DESC, created_at DESC
                LIMIT 1
                """,
                (session_id, before_sort_order),
            ).fetchone()

        if not assistant_row:
            return None

        user_row = conn.execute(
            """
            SELECT * FROM chat_messages
            WHERE session_id = ?
              AND role = 'user'
              AND sort_order < ?
            ORDER BY sort_order DESC, created_at DESC
            LIMIT 1
            """,
            (session_id, int(assistant_row["sort_order"])),
        ).fetchone()

    if not user_row:
        return None
    return {
        "userMessageId": user_row["id"],
        "assistantMessageId": assistant_row["id"],
        "userMessage": user_row["content"],
        "assistantText": assistant_row["content"],
        "toolCalls": _loads(assistant_row["tool_calls"], []),
        "runtimeEvents": _loads(assistant_row["runtime_events"], []),
        "createdAt": assistant_row["created_at"],
    }


def _memory_record_store() -> MemoryRecordStore:
    """创建复用当前数据库连接与锁的 Memory 领域存储。"""

    return MemoryRecordStore(
        ensure_db=ensure_db,
        connect=_connect,
        lock=_lock,
        utc_now=_utc_now,
        dumps=_dumps,
        loads=_loads,
        default_workspace_id=DEFAULT_WORKSPACE_ID,
        default_project_id=DEFAULT_PROJECT_ID,
    )


def upsert_memory_node(data: dict[str, Any]) -> dict[str, Any]:
    """写入或更新长期节点记忆。"""

    return _memory_record_store().upsert(data)


def get_memory_node(node_id: str) -> dict[str, Any] | None:
    return _memory_record_store().get(node_id)


def list_memory_nodes(
    *,
    workspace_id: str = "default",
    project_id: str = DEFAULT_PROJECT_ID,
    agent_id: str | None = None,
    user_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    status: str | None = "active",
    limit: int = 30,
) -> list[dict[str, Any]]:
    return _memory_record_store().list(
        workspace_id=workspace_id, project_id=project_id, agent_id=agent_id,
        user_id=user_id, date_from=date_from, date_to=date_to, status=status, limit=limit,
    )


def search_memory_nodes(
    *,
    workspace_id: str = "default",
    project_id: str = DEFAULT_PROJECT_ID,
    agent_id: str | None = None,
    user_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    query_text: str = "",
    status: str | None = "active",
    limit: int = 10,
) -> list[dict[str, Any]]:
    return _memory_record_store().search(
        workspace_id=workspace_id, project_id=project_id, agent_id=agent_id,
        user_id=user_id, date_from=date_from, date_to=date_to, query_text=query_text,
        status=status, limit=limit,
    )


def update_memory_node_vector_state(
    node_id: str,
    *,
    vector_status: str,
    vector_error: str = "",
) -> dict[str, Any] | None:
    return _memory_record_store().update_vector_state(
        node_id, vector_status=vector_status, vector_error=vector_error,
    )


def update_memory_node_status(node_id: str, *, status: str) -> dict[str, Any] | None:
    return _memory_record_store().update_status(node_id, status=status)


def mark_memory_node_used(node_id: str) -> dict[str, Any] | None:
    return _memory_record_store().mark_used(node_id)


def find_session_day_memory_node(
    *, workspace_id: str, project_id: str, agent_id: str, node_date: str, source_session_id: str
) -> dict[str, Any] | None:
    return _memory_record_store().find_session_day_node(
        workspace_id=workspace_id,
        project_id=project_id,
        agent_id=agent_id,
        node_date=node_date,
        source_session_id=source_session_id,
    )


def list_stale_active_memory_node_ids(*, before_date: str, unused_since: str, limit: int = 200) -> list[str]:
    return _memory_record_store().list_stale_active(before_date=before_date, unused_since=unused_since, limit=limit)


def record_memory_retrieval_feedback(data: dict[str, Any]) -> dict[str, Any]:
    return _memory_record_store().record_feedback(data)


def list_memory_retrieval_feedback(limit: int = 200) -> list[dict[str, Any]]:
    return _memory_record_store().list_feedback(limit)


def list_memory_retrieval_feedback_scores(limit: int = 500) -> dict[str, float]:
    return _memory_record_store().feedback_scores(limit)


def _skill_evolution_record_store() -> SkillEvolutionRecordStore:
    """创建复用当前数据库基础设施的 Skill 自进化领域存储。"""

    return SkillEvolutionRecordStore(
        ensure_db=ensure_db,
        connect=_connect,
        lock=_lock,
        utc_now=_utc_now,
        dumps=_dumps,
        loads=_loads,
        default_project_id=DEFAULT_PROJECT_ID,
    )


def upsert_skill_evolution_record(data: dict[str, Any]) -> dict[str, Any]:
    """写入或更新 Skill 自进化候选记录。"""

    return _skill_evolution_record_store().upsert(data)


def get_skill_evolution_record(record_id: str) -> dict[str, Any] | None:
    """读取单条 Skill 自进化候选记录。"""

    return _skill_evolution_record_store().get(record_id)


def list_skill_evolution_records(
    *,
    status: str | None = None,
    agent_id: str | None = None,
    project_id: str = DEFAULT_PROJECT_ID,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """按 Project、状态或 Agent 列出 Skill 自进化候选记录。"""

    return _skill_evolution_record_store().list(
        status=status,
        agent_id=agent_id,
        project_id=project_id,
        limit=limit,
    )


def mark_skill_evolution_record_registered(
    record_id: str,
    *,
    registered_skill_id: str,
) -> dict[str, Any] | None:
    """标记 Skill 自进化候选已注册为标准 Skill。"""

    return _skill_evolution_record_store().mark_registered(
        record_id,
        registered_skill_id=registered_skill_id,
    )


def update_skill_evolution_record_status(
    record_id: str,
    *,
    status: str,
    risk_notes: str | None = None,
    clear_registered_skill: bool = False,
) -> dict[str, Any] | None:
    """更新 Skill 自进化候选状态。"""

    return _skill_evolution_record_store().update_status(
        record_id,
        status=status,
        risk_notes=risk_notes,
        clear_registered_skill=clear_registered_skill,
    )


def _plan_record_store() -> PlanRecordStore:
    """创建复用当前数据库连接与锁的 Plan 领域存储。"""

    return PlanRecordStore(
        ensure_db=ensure_db,
        connect=_connect,
        lock=_lock,
        utc_now=_utc_now,
    )


def create_plan_record(plan: dict[str, Any], steps: list[dict[str, Any]]) -> dict[str, Any]:
    """创建结构化运行计划及其步骤。"""

    return _plan_record_store().create(plan, steps)


def get_plan_record(plan_id: str) -> dict[str, Any] | None:
    return _plan_record_store().get(plan_id)


def list_session_plans(session_id: str) -> list[dict[str, Any]]:
    return _plan_record_store().list_session(session_id)


def get_active_session_plan_record(session_id: str, statuses: list[str] | tuple[str, ...]) -> dict[str, Any] | None:
    """读取一个会话最新的未结束 Plan，用于 Plan Session 锁定。"""

    return _plan_record_store().get_active(session_id, statuses)


def update_plan_record(
    plan_id: str,
    *,
    title: str | None = None,
    status: str | None = None,
    markdown: str | None = None,
    current_step_id: str | None = None,
    revision: int | None = None,
    approved_revision: int | None = None,
    feedback: str | None = None,
    execution_run_id: str | None = None,
    completed_at: str | None = None,
) -> dict[str, Any] | None:
    return _plan_record_store().update_plan(
        plan_id,
        title=title,
        status=status,
        markdown=markdown,
        current_step_id=current_step_id,
        revision=revision,
        approved_revision=approved_revision,
        feedback=feedback,
        execution_run_id=execution_run_id,
        completed_at=completed_at,
    )


def publish_plan_revision_record(
    plan_id: str,
    *,
    expected_status: str,
    revision: int,
    markdown: str,
    steps: list[dict[str, Any]],
    feedback: str = "",
) -> dict[str, Any] | None:
    """原子发布一次不可变 Plan revision。"""

    return _plan_record_store().publish_revision(
        plan_id,
        expected_status=expected_status,
        revision=revision,
        markdown=markdown,
        steps=steps,
        feedback=feedback,
    )


def transition_plan_status_record(
    plan_id: str,
    *,
    expected_status: str,
    status: str,
    approved_revision: int | None = None,
    feedback: str | None = None,
    execution_run_id: str | None = None,
    completed_at: str | None = None,
) -> dict[str, Any] | None:
    """按期望状态原子迁移 Plan。"""

    return _plan_record_store().transition_status(
        plan_id,
        expected_status=expected_status,
        status=status,
        approved_revision=approved_revision,
        feedback=feedback,
        execution_run_id=execution_run_id,
        completed_at=completed_at,
    )


def update_plan_step_record(
    step_id: str,
    *,
    title: str | None = None,
    description: str | None = None,
    status: str | None = None,
    evidence: str | None = None,
    result: str | None = None,
    started_at: str | None = None,
    completed_at: str | None = None,
) -> dict[str, Any] | None:
    return _plan_record_store().update_step(
        step_id,
        title=title,
        description=description,
        status=status,
        evidence=evidence,
        result=result,
        started_at=started_at,
        completed_at=completed_at,
    )


def replace_plan_steps_record(plan_id: str, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """替换计划的全部结构化步骤。"""

    return _plan_record_store().replace_steps(plan_id, steps)


def append_plan_event_record(
    plan_id: str,
    *,
    step_id: str | None,
    event_type: str,
    content: str,
    run_event_id: str | None = None,
) -> dict[str, Any]:
    return _plan_record_store().append_event(
        plan_id,
        step_id=step_id,
        event_type=event_type,
        content=content,
        run_event_id=run_event_id,
    )


def normalized_table_counts() -> dict[str, int]:
    ensure_db()
    tables = [
        "agents", "mcp_servers", "skills",
        "chat_sessions", "chat_messages", "memory_nodes",
    ]
    with _lock, _connect() as conn:
        return {
            table: conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
            for table in tables
        }


async def init_db() -> None:
    """Initialize SQLite storage."""
    ensure_db()
    print(f"[DB] SQLite initialized at {DB_PATH}")


async def close_db() -> None:
    """No persistent process-wide connection is kept."""
    print("[DB] SQLite connections closed")


def _scheduled_job_record_store() -> ScheduledJobRecordStore:
    """创建复用当前数据库连接与锁的定时任务存储。"""

    return ScheduledJobRecordStore(
        ensure_db=ensure_db,
        connect=_connect,
        lock=_lock,
        utc_now=_utc_now,
        dumps=_dumps,
        loads=_loads,
        default_workspace_id=DEFAULT_WORKSPACE_ID,
        default_project_id=DEFAULT_PROJECT_ID,
    )


def insert_scheduled_job(record: dict[str, Any]) -> dict[str, Any]:
    """插入一条 ScheduledJob。"""

    return _scheduled_job_record_store().insert_job(record)


def update_scheduled_job(job_id: str, changes: dict[str, Any]) -> dict[str, Any] | None:
    """更新 ScheduledJob 字段。"""

    return _scheduled_job_record_store().update_job(job_id, changes)


def get_scheduled_job(job_id: str) -> dict[str, Any] | None:
    """读取一条 ScheduledJob。"""

    return _scheduled_job_record_store().get_job(job_id)


def list_scheduled_jobs(
    *,
    project_id: str | None = None,
    agent_id: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """按 Project / Agent / 状态列出 ScheduledJob。"""

    return _scheduled_job_record_store().list_jobs(
        project_id=project_id, agent_id=agent_id, status=status,
    )


def delete_scheduled_job(job_id: str) -> None:
    """删除一条 ScheduledJob，供写入校验失败时回滚。"""

    _scheduled_job_record_store().delete_job(job_id)


def insert_scheduled_job_run(record: dict[str, Any]) -> dict[str, Any]:
    """插入一次 firing。"""

    return _scheduled_job_record_store().insert_firing(record)


def update_scheduled_job_run(firing_id: str, changes: dict[str, Any]) -> dict[str, Any] | None:
    """更新 firing 字段。"""

    return _scheduled_job_record_store().update_firing(firing_id, changes)


def get_scheduled_job_run(firing_id: str) -> dict[str, Any] | None:
    """读取一次 firing。"""

    return _scheduled_job_record_store().get_firing(firing_id)


def list_scheduled_job_runs(job_id: str, *, status: str | None = None) -> list[dict[str, Any]]:
    """列出一个 Job 的 firing 历史。"""

    return _scheduled_job_record_store().list_firings(job_id, status=status)


def list_due_scheduled_jobs(now_iso: str) -> list[dict[str, Any]]:
    """列出已到期的 active Job。"""

    return _scheduled_job_record_store().list_due_jobs(now_iso)


def get_scheduled_job_run_by_key(
    job_id: str,
    scheduled_for: str,
    trigger: str,
) -> dict[str, Any] | None:
    """按 UNIQUE (job_id, scheduled_for, trigger) 读取 firing。"""

    return _scheduled_job_record_store().get_firing_by_key(job_id, scheduled_for, trigger)


def list_non_terminal_scheduled_job_runs() -> list[dict[str, Any]]:
    """列出全部非终态 firing。"""

    return _scheduled_job_record_store().list_non_terminal_firings()


def list_starting_scheduled_job_runs_without_chat_run() -> list[dict[str, Any]]:
    """列出 starting 且 chat_run_id 仍空的 firing。"""

    return _scheduled_job_record_store().list_starting_without_chat_run()


def list_scheduled_job_runs_for_scheduled_for(
    job_id: str,
    scheduled_for: str,
) -> list[dict[str, Any]]:
    """列出同一 Job 同一 scheduled_for 的 firing，不区分 trigger。"""

    return _scheduled_job_record_store().list_firings_for_scheduled_for(
        job_id, scheduled_for,
    )


def list_stuck_clock_scheduled_job_runs() -> list[dict[str, Any]]:
    """列出终态 schedule/catch_up 仍卡住 Job.next_run_at 的 firing。"""

    return _scheduled_job_record_store().list_stuck_clock_firings()


def update_scheduled_job_run_and_job(
    firing_id: str,
    firing_changes: dict[str, Any],
    job_id: str,
    job_changes: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """同一事务更新 firing 与 Job。"""

    return _scheduled_job_record_store().update_firing_and_job(
        firing_id, firing_changes, job_id, job_changes,
    )

def insert_user_notification(record: dict[str, Any]) -> dict[str, Any]:
    """写入一条应用内通知。"""

    return _scheduled_job_record_store().insert_notification(record)


def get_user_notification(note_id: str) -> dict[str, Any] | None:
    """读取一条应用内通知。"""

    return _scheduled_job_record_store().get_notification(note_id)


def list_user_notifications(
    *,
    project_id: str | None = None,
    unread_only: bool = False,
    limit: int = 50,
    before: str | None = None,
) -> list[dict[str, Any]]:
    """按时间倒序列出应用内通知。"""

    return _scheduled_job_record_store().list_notifications(
        project_id=project_id,
        unread_only=unread_only,
        limit=limit,
        before=before,
    )


def count_unread_user_notifications(*, project_id: str | None = None) -> int:
    """未读通知条数。"""

    return _scheduled_job_record_store().count_unread_notifications(project_id=project_id)


def mark_user_notification_read(note_id: str) -> dict[str, Any] | None:
    """将一条通知标为已读；不存在则返回 None。"""

    return _scheduled_job_record_store().mark_notification_read(note_id)


def mark_all_user_notifications_read(*, project_id: str | None = None) -> int:
    """将未读通知全部标为已读，返回更新行数。"""

    return _scheduled_job_record_store().mark_all_notifications_read(project_id=project_id)
