"""
SQLite-backed JSON document storage.

The current route models are Pydantic documents that were previously persisted
as JSON files. This module stores the same documents in SQLite so the API
surface can stay stable while persistence moves off flat files.
"""

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any

from src.config.settings import settings


_lock = RLock()
_initialized = False
BACKEND_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = BACKEND_ROOT.parent
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

    normalized = {**DEFAULT_GUARDRAILS, **data}
    if str(normalized.get("filesystem_scope") or "").strip() == "/":
        normalized["filesystem_scope"] = str(PROJECT_ROOT)
    return normalized

DEFAULT_PLAN_CONFIG = {
    "enabled": False,
    "mode": "structured",
    "maxSteps": 6,
    "requirePlanBeforeTools": False,
    "requireUserApproval": False,
    "allowReplanning": False,
}

DEFAULT_MODEL_CONFIG = {
    "primary": "deepseek-chat",
    "fallback": ["deepseek-reasoner"],
    "temperature": 0.7,
    "topP": 0.8,
    "maxTokens": 4096,
    "maxReactIters": 40,
}
EMPTY_DISPLAY_SNAPSHOT = {"version": 3, "mode": "single_agent", "actors": [], "blocks": []}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db_path() -> Path:
    url = settings.database_url
    prefix = "sqlite+aiosqlite:///"
    if url.startswith(prefix):
        return _resolve_sqlite_path(url[len(prefix):])
    if url.startswith("sqlite:///"):
        return _resolve_sqlite_path(url[len("sqlite:///"):])
    return BACKEND_ROOT / "data/agent_workstation.db"


def _resolve_sqlite_path(value: str) -> Path:
    """把相对 SQLite 路径固定到 backend 目录，避免启动目录不同造成空库。"""

    path = Path(value)
    if path.is_absolute():
        return path
    return BACKEND_ROOT / path


DB_PATH = _db_path()


def _connect() -> sqlite3.Connection:
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
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS workspaces (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    root_path TEXT NOT NULL,
                    instructions_file TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(workspace_id) REFERENCES workspaces(id)
                );
                CREATE INDEX IF NOT EXISTS idx_projects_workspace_status
                    ON projects(workspace_id, status, updated_at DESC);
                CREATE TABLE IF NOT EXISTS project_resource_bindings (
                    project_id TEXT NOT NULL,
                    resource_type TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    config_override TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(project_id, resource_type, resource_id),
                    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_project_resource_bindings_project
                    ON project_resource_bindings(project_id, resource_type, enabled);
                CREATE TABLE IF NOT EXISTS agents (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    avatar TEXT NOT NULL DEFAULT '',
                    description TEXT NOT NULL DEFAULT '',
                    system_prompt TEXT NOT NULL,
                    model_config TEXT NOT NULL,
                    mcp_tools TEXT NOT NULL,
                    knowledge_tools TEXT NOT NULL DEFAULT '[]',
                    skills TEXT NOT NULL,
                    tags TEXT NOT NULL,
                    plan_config TEXT NOT NULL DEFAULT '{}',
                    guardrails TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS mcp_servers (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    category TEXT NOT NULL,
                    transport TEXT NOT NULL,
                    command TEXT NOT NULL DEFAULT '',
                    url TEXT NOT NULL DEFAULT '',
                    homepage TEXT NOT NULL DEFAULT '',
                    headers TEXT NOT NULL,
                    tags TEXT NOT NULL,
                    tools TEXT NOT NULL,
                    status TEXT NOT NULL,
                    enabled INTEGER NOT NULL,
                    installed_at TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS http_tools (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    category TEXT NOT NULL DEFAULT 'custom',
                    method TEXT NOT NULL DEFAULT 'POST',
                    url TEXT NOT NULL,
                    auth_type TEXT NOT NULL DEFAULT 'none',
                    auth_config TEXT NOT NULL DEFAULT '{}',
                    headers TEXT NOT NULL DEFAULT '{}',
                    query_template TEXT NOT NULL DEFAULT '{}',
                    body_template TEXT NOT NULL DEFAULT '{}',
                    parameter_schema TEXT NOT NULL DEFAULT '{}',
                    parameter_instructions TEXT NOT NULL DEFAULT '',
                    response_mode TEXT NOT NULL DEFAULT 'full',
                    response_path TEXT NOT NULL DEFAULT '',
                    readonly INTEGER NOT NULL DEFAULT 1,
                    approval_policy TEXT NOT NULL DEFAULT 'inherit',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS skills (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    category TEXT NOT NULL,
                    trigger TEXT NOT NULL DEFAULT '',
                    content TEXT NOT NULL DEFAULT '',
                    tags TEXT NOT NULL,
                    resource_path TEXT NOT NULL DEFAULT '',
                    resources TEXT NOT NULL,
                    status TEXT NOT NULL,
                    enabled INTEGER NOT NULL,
                    installed_at TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS chat_sessions (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    agent_name TEXT NOT NULL,
                    mode TEXT NOT NULL DEFAULT 'agent',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    message_count INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS chat_messages (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    agent_id TEXT,
                    agent_name TEXT,
                    model_name TEXT,
                    error TEXT,
                    thinking TEXT NOT NULL DEFAULT '',
                    images TEXT NOT NULL,
                    tool_calls TEXT NOT NULL DEFAULT '[]',
                    skill_calls TEXT NOT NULL,
                    model_caches TEXT NOT NULL DEFAULT '[]',
                    runtime_events TEXT NOT NULL DEFAULT '[]',
                    plan_runs TEXT NOT NULL DEFAULT '[]',
                    display_snapshot TEXT NOT NULL DEFAULT '{"version":3,"mode":"single_agent","actors":[],"blocks":[]}',
                    team_snapshot TEXT,
                    created_at TEXT NOT NULL,
                    sort_order INTEGER NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS plans (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL DEFAULT '',
                    session_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    goal TEXT NOT NULL,
                    status TEXT NOT NULL,
                    markdown TEXT NOT NULL,
                    current_step_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS plan_steps (
                    id TEXT PRIMARY KEY,
                    plan_id TEXT NOT NULL,
                    step_index INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    status TEXT NOT NULL,
                    assigned_agent_id TEXT,
                    evidence TEXT NOT NULL DEFAULT '',
                    result TEXT NOT NULL DEFAULT '',
                    started_at TEXT,
                    completed_at TEXT,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(plan_id) REFERENCES plans(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS plan_events (
                    id TEXT PRIMARY KEY,
                    plan_id TEXT NOT NULL,
                    step_id TEXT,
                    run_event_id TEXT,
                    event_type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(plan_id) REFERENCES plans(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_plans_session_updated
                    ON plans(session_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_plan_steps_plan_order
                    ON plan_steps(plan_id, step_index);
                CREATE TABLE IF NOT EXISTS run_events (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    scope TEXT NOT NULL DEFAULT 'leader',
                    agent_id TEXT,
                    agent_name TEXT,
                    status TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL DEFAULT '',
                    content TEXT NOT NULL DEFAULT '',
                    payload TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY(session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_run_events_session_order
                    ON run_events(session_id, sort_order, created_at);
                CREATE TABLE IF NOT EXISTS subagent_invocations (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    tool_call_id TEXT NOT NULL DEFAULT '',
                    parent_scope TEXT NOT NULL DEFAULT 'single',
                    parent_agent_id TEXT NOT NULL,
                    parent_agent_name TEXT NOT NULL,
                    definition_scope TEXT NOT NULL DEFAULT '',
                    definition_path TEXT NOT NULL DEFAULT '',
                    subagent_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    task TEXT NOT NULL,
                    expected_output TEXT NOT NULL DEFAULT '',
                    result_preview TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_subagent_invocations_run
                    ON subagent_invocations(run_id, started_at);
                CREATE INDEX IF NOT EXISTS idx_subagent_invocations_session
                    ON subagent_invocations(session_id, started_at);
                CREATE TABLE IF NOT EXISTS chat_runs (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL DEFAULT '',
                    agent_name TEXT NOT NULL DEFAULT '',
                    task_text TEXT NOT NULL DEFAULT '',
                    mode TEXT NOT NULL DEFAULT 'agent',
                    status TEXT NOT NULL DEFAULT 'running',
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS chat_run_events (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    raw_event TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY(run_id) REFERENCES chat_runs(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS chat_run_states (
                    run_id TEXT PRIMARY KEY,
                    state TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES chat_runs(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_chat_runs_updated
                    ON chat_runs(updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_chat_run_events_order
                    ON chat_run_events(run_id, sort_order, created_at);
                CREATE TABLE IF NOT EXISTS workflow_runs (
                    id TEXT PRIMARY KEY,
                    schema_version TEXT NOT NULL DEFAULT 'workflow.v1',
                    title TEXT NOT NULL,
                    goal TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'draft',
                    current_node_id TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS workflow_nodes (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    node_key TEXT NOT NULL,
                    node_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    instruction TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending',
                    agent_id TEXT NOT NULL DEFAULT '',
                    team_id TEXT NOT NULL DEFAULT '',
                    summary TEXT NOT NULL DEFAULT '',
                    output TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    position INTEGER NOT NULL DEFAULT 0,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    started_at TEXT,
                    completed_at TEXT,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES workflow_runs(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS workflow_edges (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    from_node_id TEXT NOT NULL,
                    to_node_id TEXT NOT NULL,
                    condition TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES workflow_runs(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS workflow_events (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    node_id TEXT NOT NULL DEFAULT '',
                    event_type TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL DEFAULT '',
                    content TEXT NOT NULL DEFAULT '',
                    payload TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY(run_id) REFERENCES workflow_runs(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS workflow_artifacts (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    node_id TEXT NOT NULL DEFAULT '',
                    artifact_type TEXT NOT NULL DEFAULT 'text',
                    title TEXT NOT NULL,
                    content TEXT NOT NULL DEFAULT '',
                    uri TEXT NOT NULL DEFAULT '',
                    metadata TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES workflow_runs(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_workflow_runs_updated
                    ON workflow_runs(updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_workflow_nodes_run_position
                    ON workflow_nodes(run_id, position ASC);
                CREATE INDEX IF NOT EXISTS idx_workflow_edges_run
                    ON workflow_edges(run_id);
                CREATE INDEX IF NOT EXISTS idx_workflow_events_run_order
                    ON workflow_events(run_id, sort_order ASC, created_at ASC);
                CREATE TABLE IF NOT EXISTS skill_runs (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    skill_id TEXT NOT NULL,
                    skill_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    current_step TEXT NOT NULL DEFAULT '',
                    collected_inputs TEXT NOT NULL DEFAULT '{}',
                    pending_interaction_id TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_skill_runs_session_agent_status
                    ON skill_runs(session_id, agent_id, status, updated_at);
                CREATE TABLE IF NOT EXISTS memory_nodes (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL DEFAULT 'default',
                    project_id TEXT NOT NULL DEFAULT 'default',
                    agent_id TEXT NOT NULL,
                    user_id TEXT NOT NULL DEFAULT 'default',
                    node_date TEXT NOT NULL,
                    date_range_start TEXT NOT NULL,
                    date_range_end TEXT NOT NULL,
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    conversation_digest TEXT NOT NULL DEFAULT '',
                    search_text TEXT NOT NULL DEFAULT '',
                    user_inputs TEXT NOT NULL DEFAULT '[]',
                    agent_final_outputs TEXT NOT NULL DEFAULT '[]',
                    tags TEXT NOT NULL DEFAULT '[]',
                    source TEXT NOT NULL DEFAULT 'chat_run',
                    source_session_id TEXT NOT NULL DEFAULT '',
                    source_run_id TEXT NOT NULL DEFAULT '',
                    source_message_id TEXT NOT NULL DEFAULT '',
                    confidence REAL NOT NULL DEFAULT 1,
                    status TEXT NOT NULL DEFAULT 'active',
                    pinned INTEGER NOT NULL DEFAULT 0,
                    vector_status TEXT NOT NULL DEFAULT 'pending',
                    vector_error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_used_at TEXT,
                    use_count INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_memory_nodes_scope_date
                    ON memory_nodes(workspace_id, project_id, agent_id, node_date DESC);
                CREATE INDEX IF NOT EXISTS idx_memory_nodes_status_date
                    ON memory_nodes(status, node_date DESC);
                CREATE INDEX IF NOT EXISTS idx_memory_nodes_source_run
                    ON memory_nodes(source_run_id);
                CREATE TABLE IF NOT EXISTS memory_retrieval_feedback (
                    id TEXT PRIMARY KEY,
                    audit_event_id TEXT NOT NULL DEFAULT '',
                    node_id TEXT NOT NULL,
                    rating TEXT NOT NULL,
                    query TEXT NOT NULL DEFAULT '',
                    agent_id TEXT NOT NULL DEFAULT '',
                    session_id TEXT NOT NULL DEFAULT '',
                    reason TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(node_id) REFERENCES memory_nodes(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_memory_retrieval_feedback_node
                    ON memory_retrieval_feedback(node_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_memory_retrieval_feedback_created
                    ON memory_retrieval_feedback(created_at DESC);
                CREATE TABLE IF NOT EXISTS skill_evolution_records (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL DEFAULT 'default',
                    project_id TEXT NOT NULL DEFAULT 'default',
                    agent_id TEXT NOT NULL,
                    agent_name TEXT NOT NULL DEFAULT '',
                    team_id TEXT NOT NULL DEFAULT '',
                    user_id TEXT NOT NULL DEFAULT 'default',
                    source_session_id TEXT NOT NULL DEFAULT '',
                    source_run_id TEXT NOT NULL DEFAULT '',
                    source_message_id TEXT NOT NULL DEFAULT '',
                    router_gate TEXT NOT NULL DEFAULT '{}',
                    workflow_evidence TEXT NOT NULL DEFAULT '{}',
                    reusable INTEGER NOT NULL DEFAULT 0,
                    reusable_reason TEXT NOT NULL DEFAULT '',
                    trigger_description TEXT NOT NULL DEFAULT '',
                    inputs_schema TEXT NOT NULL DEFAULT '{}',
                    draft_skill_name TEXT NOT NULL DEFAULT '',
                    draft_skill_description TEXT NOT NULL DEFAULT '',
                    draft_skill_content TEXT NOT NULL DEFAULT '',
                    draft_tags TEXT NOT NULL DEFAULT '[]',
                    granularity TEXT NOT NULL DEFAULT '',
                    requires_workflow INTEGER NOT NULL DEFAULT 0,
                    suggested_split TEXT NOT NULL DEFAULT '[]',
                    variable_flow TEXT NOT NULL DEFAULT '[]',
                    risk_notes TEXT NOT NULL DEFAULT '',
                    confidence REAL NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'skipped',
                    registered_skill_id TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_skill_evolution_records_status_updated
                    ON skill_evolution_records(status, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_skill_evolution_records_source
                    ON skill_evolution_records(source_session_id, source_run_id, source_message_id);
                CREATE INDEX IF NOT EXISTS idx_skill_evolution_records_agent
                    ON skill_evolution_records(agent_id, updated_at DESC);
            """)
            _ensure_column(conn, "chat_messages", "tool_calls", "TEXT NOT NULL DEFAULT '[]'")
            _ensure_column(conn, "chat_messages", "thinking", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(conn, "chat_messages", "model_caches", "TEXT NOT NULL DEFAULT '[]'")
            _ensure_column(conn, "chat_messages", "runtime_events", "TEXT NOT NULL DEFAULT '[]'")
            _ensure_column(conn, "chat_messages", "plan_runs", "TEXT NOT NULL DEFAULT '[]'")
            _ensure_column(conn, "chat_messages", "display_snapshot", "TEXT NOT NULL DEFAULT '{\"version\":2,\"blocks\":[]}'")
            _ensure_column(conn, "chat_messages", "team_snapshot", "TEXT")
            _ensure_column(conn, "chat_sessions", "mode", "TEXT NOT NULL DEFAULT 'agent'")
            _ensure_column(conn, "agents", "workspace_id", "TEXT NOT NULL DEFAULT 'default'")
            _ensure_column(conn, "mcp_servers", "workspace_id", "TEXT NOT NULL DEFAULT 'default'")
            _ensure_column(conn, "http_tools", "workspace_id", "TEXT NOT NULL DEFAULT 'default'")
            _ensure_column(conn, "skills", "workspace_id", "TEXT NOT NULL DEFAULT 'default'")
            _ensure_column(conn, "chat_sessions", "workspace_id", "TEXT NOT NULL DEFAULT 'default'")
            _ensure_column(conn, "chat_sessions", "project_id", "TEXT NOT NULL DEFAULT 'default'")
            _ensure_column(conn, "chat_runs", "workspace_id", "TEXT NOT NULL DEFAULT 'default'")
            _ensure_column(conn, "chat_runs", "project_id", "TEXT NOT NULL DEFAULT 'default'")
            _ensure_column(conn, "run_events", "workspace_id", "TEXT NOT NULL DEFAULT 'default'")
            _ensure_column(conn, "run_events", "project_id", "TEXT NOT NULL DEFAULT 'default'")
            _ensure_column(conn, "memory_nodes", "workspace_id", "TEXT NOT NULL DEFAULT 'default'")
            _ensure_column(conn, "memory_nodes", "project_id", "TEXT NOT NULL DEFAULT 'default'")
            _ensure_column(conn, "subagent_invocations", "workspace_id", "TEXT NOT NULL DEFAULT 'default'")
            _ensure_column(conn, "subagent_invocations", "project_id", "TEXT NOT NULL DEFAULT 'default'")
            _drop_column_if_exists(conn, "agents", "callable_agents")
            _drop_column_if_exists(conn, "agents", "max_depth")
            _drop_column_if_exists(conn, "chat_messages", "subagent_calls")
            _drop_column_if_exists(conn, "chat_messages", "team_activities")
            _drop_column_if_exists(conn, "chat_sessions", "team_id")
            _drop_column_if_exists(conn, "chat_sessions", "team_name")
            _drop_column_if_exists(conn, "plans", "team_id")
            _drop_column_if_exists(conn, "run_events", "team_activity_id")
            conn.execute("DROP TABLE IF EXISTS team_members")
            conn.execute("DROP TABLE IF EXISTS teams")
            conn.execute("DELETE FROM json_store WHERE namespace = 'team_templates'")
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


def _workspace_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "status": row["status"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


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


def list_workspaces(*, include_archived: bool = False) -> list[dict[str, Any]]:
    """列出 Workspace。"""

    ensure_db()
    query = "SELECT * FROM workspaces"
    params: tuple[Any, ...] = ()
    if not include_archived:
        query += " WHERE status = ?"
        params = ("active",)
    query += " ORDER BY updated_at DESC, id ASC"
    with _lock, _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_workspace_from_row(row) for row in rows]


def get_workspace(workspace_id: str) -> dict[str, Any] | None:
    """读取一个 Workspace。"""

    ensure_db()
    with _lock, _connect() as conn:
        row = conn.execute("SELECT * FROM workspaces WHERE id = ?", (workspace_id,)).fetchone()
    return _workspace_from_row(row) if row else None


def create_workspace(data: dict[str, Any]) -> dict[str, Any]:
    """创建 Workspace。"""

    ensure_db()
    now = _utc_now()
    workspace_id = str(data.get("id") or f"ws_{uuid.uuid4().hex}")
    with _lock, _connect() as conn:
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
    return get_workspace(workspace_id) or {}


def list_projects(
    workspace_id: str | None = None,
    *,
    include_archived: bool = False,
) -> list[dict[str, Any]]:
    """列出 Project，可按 Workspace 过滤。"""

    ensure_db()
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
    with _lock, _connect() as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
    return [_project_from_row(row) for row in rows]


def get_project(project_id: str) -> dict[str, Any] | None:
    """读取一个 Project。"""

    ensure_db()
    with _lock, _connect() as conn:
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    return _project_from_row(row) if row else None


def create_project(data: dict[str, Any]) -> dict[str, Any]:
    """创建 Project。"""

    ensure_db()
    now = _utc_now()
    project_id = str(data.get("id") or f"proj_{uuid.uuid4().hex}")
    workspace_id = str(data.get("workspace_id") or data.get("workspaceId") or DEFAULT_WORKSPACE_ID)
    with _lock, _connect() as conn:
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
                str(data.get("root_path") or data.get("rootPath") or PROJECT_ROOT),
                str(data.get("instructions_file") or data.get("instructionsFile") or ""),
                str(data.get("status") or "active"),
                now,
                now,
            ),
        )
        _bind_workspace_resources(
            conn,
            project_id=project_id,
            workspace_id=workspace_id,
            now=now,
        )
        conn.commit()
    return get_project(project_id) or {}


def update_project(project_id: str, changes: dict[str, Any]) -> dict[str, Any] | None:
    """更新 Project 可编辑字段。"""

    ensure_db()
    existing = get_project(project_id)
    if existing is None:
        return None
    values = {
        "name": str(changes.get("name", existing["name"])),
        "description": str(changes.get("description", existing["description"])),
        "root_path": str(changes.get("root_path", changes.get("rootPath", existing["rootPath"]))),
        "instructions_file": str(
            changes.get("instructions_file", changes.get("instructionsFile", existing["instructionsFile"])),
        ),
        "status": str(changes.get("status", existing["status"])),
    }
    with _lock, _connect() as conn:
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
                _utc_now(),
                project_id,
            ),
        )
        conn.commit()
    return get_project(project_id)


def list_project_resource_bindings(project_id: str) -> list[dict[str, Any]]:
    """列出 Project 资源绑定。"""

    ensure_db()
    with _lock, _connect() as conn:
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
            "configOverride": _loads(row["config_override"], {}),
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }
        for row in rows
    ]


def get_workspace_resource(
    resource_type: str,
    resource_id: str,
) -> dict[str, str] | None:
    """读取持久资源的 Workspace 归属。"""

    ensure_db()
    table = _PROJECT_RESOURCE_TABLES.get(resource_type)
    if table is None:
        return None
    with _lock, _connect() as conn:
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
                return {"id": str(legacy["key"]), "workspaceId": DEFAULT_WORKSPACE_ID}
    if row is None:
        return None
    return {
        "id": str(row["id"]),
        "workspaceId": str(row["workspace_id"] or DEFAULT_WORKSPACE_ID),
    }


def upsert_project_resource_binding(data: dict[str, Any]) -> dict[str, Any]:
    """创建或更新一个 Project 资源绑定。"""

    ensure_db()
    project_id = str(data.get("project_id") or data.get("projectId") or "")
    resource_type = str(data.get("resource_type") or data.get("resourceType") or "")
    resource_id = str(data.get("resource_id") or data.get("resourceId") or "")
    now = _utc_now()
    with _lock, _connect() as conn:
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
                _dumps(data.get("config_override") or data.get("configOverride") or {}),
                now,
                now,
            ),
        )
        conn.commit()
    return next(
        item
        for item in list_project_resource_bindings(project_id)
        if item["resourceType"] == resource_type and item["resourceId"] == resource_id
    )


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
def list_json_records(namespace: str) -> list[dict[str, Any]]:
    """List all JSON documents in a namespace."""
    ensure_db()
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT value FROM json_store WHERE namespace = ? ORDER BY updated_at ASC",
            (namespace,),
        ).fetchall()
    return [json.loads(row["value"]) for row in rows]


def get_json_record(namespace: str, key: str) -> dict[str, Any] | None:
    """Read one JSON document."""
    ensure_db()
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT value FROM json_store WHERE namespace = ? AND key = ?",
            (namespace, key),
        ).fetchone()
    return json.loads(row["value"]) if row else None


def upsert_json_record(namespace: str, key: str, value: dict[str, Any]) -> None:
    """Insert or update one JSON document."""
    ensure_db()
    now = _utc_now()
    payload = json.dumps(value, ensure_ascii=False, default=str)
    with _lock, _connect() as conn:
        conn.execute(
            """
            INSERT INTO json_store(namespace, key, value, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(namespace, key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (namespace, key, payload, now, now),
        )
        conn.commit()


def replace_json_namespace(namespace: str, records: dict[str, dict[str, Any]]) -> None:
    """Replace all records in one namespace atomically."""
    ensure_db()
    now = _utc_now()
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM json_store WHERE namespace = ?", (namespace,))
        conn.executemany(
            """
            INSERT INTO json_store(namespace, key, value, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (namespace, key, json.dumps(value, ensure_ascii=False, default=str), now, now)
                for key, value in records.items()
            ],
        )
        conn.commit()


def delete_json_record(namespace: str, key: str) -> None:
    """Delete one JSON document."""
    ensure_db()
    with _lock, _connect() as conn:
        conn.execute(
            "DELETE FROM json_store WHERE namespace = ? AND key = ?",
            (namespace, key),
        )
        conn.commit()


def namespace_has_records(namespace: str) -> bool:
    """Return whether a namespace contains records."""
    ensure_db()
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM json_store WHERE namespace = ? LIMIT 1",
            (namespace,),
        ).fetchone()
    return row is not None


def migrate_json_list_file(
    namespace: str,
    path: str | Path,
    key_field: str = "id",
) -> None:
    """Import an existing JSON list file into SQLite if namespace is empty."""
    file_path = Path(path)
    if namespace_has_records(namespace) or not file_path.exists():
        return

    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except Exception:
        return
    if not isinstance(data, list):
        return

    records = {
        str(item[key_field]): item
        for item in data
        if isinstance(item, dict) and item.get(key_field)
    }
    if records:
        replace_json_namespace(namespace, records)


def migrate_json_object_file(
    namespace: str,
    key: str,
    path: str | Path,
) -> None:
    """Import an existing JSON object file into SQLite if the record is empty."""
    file_path = Path(path)
    if get_json_record(namespace, key) is not None or not file_path.exists():
        return

    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except Exception:
        return
    if isinstance(data, dict):
        upsert_json_record(namespace, key, data)


def list_agents() -> list[dict[str, Any]]:
    ensure_db()
    with _lock, _connect() as conn:
        rows = conn.execute("SELECT * FROM agents ORDER BY created_at ASC").fetchall()
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "avatar": row["avatar"],
            "description": row["description"],
            "system_prompt": row["system_prompt"],
            "modelConfig": (
                {**DEFAULT_MODEL_CONFIG, **model_config}
                if isinstance((model_config := _loads(row["model_config"], {})), dict)
                else DEFAULT_MODEL_CONFIG
            ),
            "mcpTools": _loads(row["mcp_tools"], []),
            "knowledgeTools": _loads(row["knowledge_tools"], []),
            "skills": _loads(row["skills"], []),
            "tags": _loads(row["tags"], []),
            "planConfig": (
                {**DEFAULT_PLAN_CONFIG, **plan_config}
                if isinstance((plan_config := _loads(row["plan_config"], {})), dict)
                else DEFAULT_PLAN_CONFIG
            ),
            "guardrails": (
                _normalize_guardrails(guardrails)
                if isinstance((guardrails := _loads(row["guardrails"], {})), dict)
                else DEFAULT_GUARDRAILS
            ),
            "status": row["status"],
            "workspaceId": row["workspace_id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        for row in rows
    ]


def upsert_agent(data: dict[str, Any]) -> None:
    ensure_db()
    with _lock, _connect() as conn:
        column_names = [
            "id", "name", "avatar", "description", "system_prompt", "model_config",
            "mcp_tools", "knowledge_tools", "skills", "tags",
            "plan_config", "guardrails", "status", "created_at", "updated_at",
            "workspace_id",
        ]
        update_columns = [
            "name", "avatar", "description", "system_prompt", "model_config",
            "mcp_tools", "knowledge_tools", "skills", "tags",
            "plan_config", "guardrails", "status", "updated_at",
            "workspace_id",
        ]
        values = [
            data["id"], data["name"], data.get("avatar", ""),
            data.get("description", ""), data.get("system_prompt", ""),
            _dumps(data.get("modelConfig", {})), _dumps(data.get("mcpTools", [])),
            _dumps(data.get("knowledgeTools", [])),
            _dumps(data.get("skills", [])), _dumps(data.get("tags", [])),
        ]
        values.extend([
            _dumps(data.get("planConfig", DEFAULT_PLAN_CONFIG)),
            _dumps(data.get("guardrails", {})), data.get("status", "idle"),
            data.get("created_at") or _utc_now(), data.get("updated_at") or _utc_now(),
            data.get("workspaceId", DEFAULT_WORKSPACE_ID),
        ])
        conn.execute(
            f"""
            INSERT INTO agents({", ".join(column_names)})
            VALUES ({", ".join("?" for _ in column_names)})
            ON CONFLICT(id) DO UPDATE SET
                {", ".join(f"{column}=excluded.{column}" for column in update_columns)}
            """,
            tuple(values),
        )
        conn.commit()


def replace_agents(records: dict[str, dict[str, Any]]) -> None:
    ensure_db()
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM agents")
        conn.commit()
    for record in records.values():
        upsert_agent(record)


def delete_agent(agent_id: str) -> None:
    ensure_db()
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM agents WHERE id = ?", (agent_id,))
        conn.commit()


def list_mcp_servers() -> list[dict[str, Any]]:
    ensure_db()
    with _lock, _connect() as conn:
        rows = conn.execute("SELECT * FROM mcp_servers ORDER BY updated_at ASC").fetchall()
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "category": row["category"],
            "transport": row["transport"],
            "command": row["command"],
            "url": row["url"],
            "homepage": row["homepage"],
            "headers": _loads(row["headers"], {}),
            "tags": _loads(row["tags"], []),
            "tools": _loads(row["tools"], []),
            "status": row["status"],
            "enabled": bool(row["enabled"]),
            "installedAt": row["installed_at"],
            "updatedAt": row["updated_at"],
            "workspaceId": row["workspace_id"],
        }
        for row in rows
    ]


def replace_mcp_servers(records: dict[str, dict[str, Any]]) -> None:
    ensure_db()
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM mcp_servers")
        conn.executemany(
            """
            INSERT INTO mcp_servers (
                id, name, description, category, transport, command, url, homepage,
                headers, tags, tools, status, enabled, installed_at, updated_at, workspace_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    data["id"], data["name"], data.get("description", ""),
                    data.get("category", "custom"), data.get("transport", "sse"),
                    data.get("command", ""), data.get("url", ""),
                    data.get("homepage", ""), _dumps(data.get("headers", {})),
                    _dumps(data.get("tags", [])), _dumps(data.get("tools", [])),
                    data.get("status", "available"), int(bool(data.get("enabled", False))),
                    data.get("installedAt"), data.get("updatedAt") or _utc_now(),
                    data.get("workspaceId", DEFAULT_WORKSPACE_ID),
                )
                for data in records.values()
            ],
        )
        conn.commit()


def list_http_tools() -> list[dict[str, Any]]:
    """List configurable HTTP tools."""
    ensure_db()
    with _lock, _connect() as conn:
        rows = conn.execute("SELECT * FROM http_tools ORDER BY updated_at DESC").fetchall()
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "category": row["category"],
            "method": row["method"],
            "url": row["url"],
            "authType": row["auth_type"],
            "authConfig": _loads(row["auth_config"], {}),
            "headers": _loads(row["headers"], {}),
            "queryTemplate": _loads(row["query_template"], {}),
            "bodyTemplate": _loads(row["body_template"], {}),
            "parameterSchema": _loads(row["parameter_schema"], {}),
            "parameterInstructions": row["parameter_instructions"],
            "responseMode": row["response_mode"],
            "responsePath": row["response_path"],
            "readonly": bool(row["readonly"]),
            "approvalPolicy": row["approval_policy"],
            "enabled": bool(row["enabled"]),
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "workspaceId": row["workspace_id"],
        }
        for row in rows
    ]


def upsert_http_tool(data: dict[str, Any]) -> dict[str, Any]:
    """Insert or update one configurable HTTP tool."""
    ensure_db()
    now = _utc_now()
    created_at = data.get("createdAt") or now
    updated_at = data.get("updatedAt") or now
    with _lock, _connect() as conn:
        conn.execute(
            """
            INSERT INTO http_tools(
                id, name, description, category, method, url, auth_type,
                auth_config, headers, query_template, body_template,
                parameter_schema, parameter_instructions, response_mode,
                response_path, readonly, approval_policy, enabled,
                created_at, updated_at, workspace_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,
                description=excluded.description,
                category=excluded.category,
                method=excluded.method,
                url=excluded.url,
                auth_type=excluded.auth_type,
                auth_config=excluded.auth_config,
                headers=excluded.headers,
                query_template=excluded.query_template,
                body_template=excluded.body_template,
                parameter_schema=excluded.parameter_schema,
                parameter_instructions=excluded.parameter_instructions,
                response_mode=excluded.response_mode,
                response_path=excluded.response_path,
                readonly=excluded.readonly,
                approval_policy=excluded.approval_policy,
                enabled=excluded.enabled,
                updated_at=excluded.updated_at
            """,
            (
                data["id"],
                data["name"],
                data.get("description", ""),
                data.get("category", "custom"),
                data.get("method", "POST").upper(),
                data["url"],
                data.get("authType", "none"),
                _dumps(data.get("authConfig", {})),
                _dumps(data.get("headers", {})),
                _dumps(data.get("queryTemplate", {})),
                _dumps(data.get("bodyTemplate", {})),
                _dumps(data.get("parameterSchema", {})),
                data.get("parameterInstructions", ""),
                data.get("responseMode", "full"),
                data.get("responsePath", ""),
                1 if data.get("readonly", True) else 0,
                data.get("approvalPolicy", "inherit"),
                1 if data.get("enabled", True) else 0,
                created_at,
                updated_at,
                data.get("workspaceId", DEFAULT_WORKSPACE_ID),
            ),
        )
        conn.commit()
    return next(item for item in list_http_tools() if item["id"] == data["id"])


def delete_http_tool(tool_id: str) -> bool:
    """Delete one configurable HTTP tool."""
    ensure_db()
    with _lock, _connect() as conn:
        cursor = conn.execute("DELETE FROM http_tools WHERE id = ?", (tool_id,))
        conn.commit()
        return cursor.rowcount > 0


def list_skills() -> list[dict[str, Any]]:
    ensure_db()
    with _lock, _connect() as conn:
        rows = conn.execute("SELECT * FROM skills ORDER BY updated_at ASC").fetchall()
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "category": row["category"],
            "trigger": row["trigger"],
            "content": row["content"],
            "tags": _loads(row["tags"], []),
            "resourcePath": row["resource_path"],
            "resources": _loads(row["resources"], []),
            "status": row["status"],
            "enabled": bool(row["enabled"]),
            "installedAt": row["installed_at"],
            "updatedAt": row["updated_at"],
            "workspaceId": row["workspace_id"],
        }
        for row in rows
    ]


def replace_skills(records: dict[str, dict[str, Any]]) -> None:
    ensure_db()
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM skills")
        conn.executemany(
            """
            INSERT INTO skills (
                id, name, description, category, trigger, content, tags,
                resource_path, resources, status, enabled, installed_at, updated_at, workspace_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    data["id"], data["name"], data.get("description", ""),
                    data.get("category", "custom"), data.get("trigger", ""),
                    data.get("content", ""), _dumps(data.get("tags", [])),
                    data.get("resourcePath", ""), _dumps(data.get("resources", [])),
                    data.get("status", "available"), int(bool(data.get("enabled", False))),
                    data.get("installedAt"), data.get("updatedAt") or _utc_now(),
                    data.get("workspaceId", DEFAULT_WORKSPACE_ID),
                )
                for data in records.values()
            ],
        )
        conn.commit()


def get_active_skill_run(session_id: str, agent_id: str) -> dict[str, Any] | None:
    ensure_db()
    with _lock, _connect() as conn:
        row = conn.execute(
            """
            SELECT * FROM skill_runs
            WHERE session_id = ?
              AND agent_id = ?
              AND status IN ('active', 'waiting_user')
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (session_id, agent_id),
        ).fetchone()
    return _skill_run_from_row(row) if row else None


def get_skill_run_by_pending_interaction(interaction_id: str) -> dict[str, Any] | None:
    ensure_db()
    with _lock, _connect() as conn:
        row = conn.execute(
            """
            SELECT * FROM skill_runs
            WHERE pending_interaction_id = ?
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (interaction_id,),
        ).fetchone()
    return _skill_run_from_row(row) if row else None


def _skill_run_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "sessionId": row["session_id"],
        "agentId": row["agent_id"],
        "skillId": row["skill_id"],
        "skillName": row["skill_name"],
        "status": row["status"],
        "currentStep": row["current_step"],
        "collectedInputs": _loads(row["collected_inputs"], {}),
        "pendingInteractionId": row["pending_interaction_id"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def upsert_skill_run(record: dict[str, Any]) -> dict[str, Any]:
    ensure_db()
    now = _utc_now()
    run_id = str(record.get("id") or f"skill-run-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}")
    created_at = str(record.get("createdAt") or now)
    payload = {
        "id": run_id,
        "session_id": str(record.get("sessionId") or record.get("session_id") or ""),
        "agent_id": str(record.get("agentId") or record.get("agent_id") or ""),
        "skill_id": str(record.get("skillId") or record.get("skill_id") or ""),
        "skill_name": str(record.get("skillName") or record.get("skill_name") or ""),
        "status": str(record.get("status") or "active"),
        "current_step": str(record.get("currentStep") or record.get("current_step") or ""),
        "collected_inputs": _dumps(record.get("collectedInputs") or record.get("collected_inputs") or {}),
        "pending_interaction_id": str(record.get("pendingInteractionId") or record.get("pending_interaction_id") or ""),
        "created_at": created_at,
        "updated_at": now,
    }
    with _lock, _connect() as conn:
        conn.execute(
            """
            INSERT INTO skill_runs (
                id, session_id, agent_id, skill_id, skill_name, status,
                current_step, collected_inputs, pending_interaction_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                status = excluded.status,
                current_step = excluded.current_step,
                collected_inputs = excluded.collected_inputs,
                pending_interaction_id = excluded.pending_interaction_id,
                updated_at = excluded.updated_at
            """,
            (
                payload["id"], payload["session_id"], payload["agent_id"],
                payload["skill_id"], payload["skill_name"], payload["status"],
                payload["current_step"], payload["collected_inputs"],
                payload["pending_interaction_id"], payload["created_at"],
                payload["updated_at"],
            ),
        )
        conn.commit()
    return get_active_skill_run(payload["session_id"], payload["agent_id"]) or {
        "id": payload["id"],
        "sessionId": payload["session_id"],
        "agentId": payload["agent_id"],
        "skillId": payload["skill_id"],
        "skillName": payload["skill_name"],
        "status": payload["status"],
        "currentStep": payload["current_step"],
        "collectedInputs": _loads(payload["collected_inputs"], {}),
        "pendingInteractionId": payload["pending_interaction_id"],
        "createdAt": payload["created_at"],
        "updatedAt": payload["updated_at"],
    }


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
            "teamSnapshot": _loads(row["team_snapshot"], None),
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
                id, title, agent_id, agent_name, mode, workspace_id, project_id,
                created_at, updated_at, message_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    item["id"], item["title"], item["agent_id"], item["agent_name"],
                    item.get("mode", "agent"),
                    item.get("workspace_id", item.get("workspaceId", DEFAULT_WORKSPACE_ID)),
                    item.get("project_id", item.get("projectId", DEFAULT_PROJECT_ID)),
                    item["created_at"],
                    item["updated_at"], item.get("message_count", 0),
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
                    _dumps(item.get("teamSnapshot", item.get("team_snapshot"))) if item.get("teamSnapshot", item.get("team_snapshot")) else None,
                    item["created_at"], index,
                ))
        conn.executemany(
            """
            INSERT INTO chat_messages (
                id, session_id, role, content, agent_id, agent_name, model_name,
                error, thinking, images, tool_calls, skill_calls,
                model_caches, runtime_events, plan_runs,
                display_snapshot, team_snapshot, created_at,
                sort_order
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()


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
    ensure_db()
    event_id = f"evt-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
    created_at = _utc_now()
    with _lock, _connect() as conn:
        ownership = conn.execute(
            "SELECT workspace_id, project_id FROM chat_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        resolved_workspace_id = workspace_id or (
            str(ownership["workspace_id"]) if ownership else DEFAULT_WORKSPACE_ID
        )
        resolved_project_id = project_id or (
            str(ownership["project_id"]) if ownership else DEFAULT_PROJECT_ID
        )
        row = conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 AS next_order FROM run_events WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        sort_order = int(row["next_order"] if row else 0)
        conn.execute(
            """
            INSERT INTO run_events (
                id, session_id, workspace_id, project_id, event_type, scope, agent_id, agent_name,
                status, title, content, payload, created_at,
                sort_order
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                session_id,
                resolved_workspace_id,
                resolved_project_id,
                event_type,
                scope,
                agent_id,
                agent_name,
                status,
                title,
                content,
                _dumps(payload or {}),
                created_at,
                sort_order,
            ),
        )
        conn.commit()
    return {
        "id": event_id,
        "sessionId": session_id,
        "workspaceId": resolved_workspace_id,
        "projectId": resolved_project_id,
        "eventType": event_type,
        "scope": scope,
        "agentId": agent_id,
        "agentName": agent_name,
        "status": status,
        "title": title,
        "content": content,
        "payload": payload or {},
        "createdAt": created_at,
        "sortOrder": sort_order,
    }


def list_run_events(
    session_id: str | None = None,
    *,
    include_payload: bool = True,
    limit: int | None = None,
    offset: int = 0,
    latest: bool = False,
) -> list[dict[str, Any]]:
    """List normalized runtime events, optionally scoped to one session."""
    ensure_db()
    safe_offset = max(0, int(offset or 0))
    with _lock, _connect() as conn:
        if session_id:
            if limit and limit > 0 and latest:
                rows = conn.execute(
                    """
                    SELECT * FROM (
                        SELECT * FROM run_events
                        WHERE session_id = ?
                        ORDER BY sort_order DESC, created_at DESC
                        LIMIT ? OFFSET ?
                    )
                    ORDER BY sort_order ASC, created_at ASC
                    """,
                    (session_id, limit, safe_offset),
                ).fetchall()
            elif limit and limit > 0:
                rows = conn.execute(
                    """
                    SELECT * FROM run_events
                    WHERE session_id = ?
                    ORDER BY sort_order ASC, created_at ASC
                    LIMIT ? OFFSET ?
                    """,
                    (session_id, limit, safe_offset),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM run_events WHERE session_id = ? ORDER BY sort_order ASC, created_at ASC",
                    (session_id,),
                ).fetchall()
        else:
            if limit and limit > 0:
                rows = conn.execute(
                    "SELECT * FROM run_events ORDER BY created_at DESC, sort_order DESC LIMIT ? OFFSET ?",
                    (limit, safe_offset),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM run_events ORDER BY created_at DESC, sort_order DESC"
                ).fetchall()
    return [
        {
            "id": row["id"],
            "sessionId": row["session_id"],
            "workspaceId": row["workspace_id"],
            "projectId": row["project_id"],
            "eventType": row["event_type"],
            "scope": row["scope"],
            "agentId": row["agent_id"],
            "agentName": row["agent_name"],
            "status": row["status"],
            "title": row["title"],
            "content": row["content"],
            "payload": _loads(row["payload"], {}) if include_payload else {},
            "createdAt": row["created_at"],
            "sortOrder": row["sort_order"],
        }
        for row in rows
    ]


def create_subagent_invocation(data: dict[str, Any]) -> dict[str, Any]:
    """创建 Subagent invocation 审计记录。"""

    ensure_db()
    invocation_id = data.get("id") or f"subagent-{uuid.uuid4().hex}"
    now = _utc_now()
    record = {
        "id": invocation_id,
        "run_id": data.get("run_id") or "",
        "session_id": data.get("session_id") or "",
        "workspace_id": data.get("workspace_id") or DEFAULT_WORKSPACE_ID,
        "project_id": data.get("project_id") or DEFAULT_PROJECT_ID,
        "tool_call_id": data.get("tool_call_id") or "",
        "parent_scope": data.get("parent_scope") or "single",
        "parent_agent_id": data.get("parent_agent_id") or "",
        "parent_agent_name": data.get("parent_agent_name") or "",
        "definition_scope": data.get("definition_scope") or "",
        "definition_path": data.get("definition_path") or "",
        "subagent_name": data.get("subagent_name") or "",
        "status": data.get("status") or "running",
        "task": data.get("task") or "",
        "expected_output": data.get("expected_output") or "",
        "result_preview": data.get("result_preview") or "",
        "error": data.get("error") or "",
        "started_at": data.get("started_at") or now,
        "completed_at": data.get("completed_at"),
        "updated_at": data.get("updated_at") or now,
    }
    with _lock, _connect() as conn:
        conn.execute(
            """
            INSERT INTO subagent_invocations (
                id, run_id, session_id, workspace_id, project_id, tool_call_id, parent_scope,
                parent_agent_id, parent_agent_name, definition_scope,
                definition_path, subagent_name, status, task, expected_output,
                result_preview, error, started_at, completed_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["id"],
                record["run_id"],
                record["session_id"],
                record["workspace_id"],
                record["project_id"],
                record["tool_call_id"],
                record["parent_scope"],
                record["parent_agent_id"],
                record["parent_agent_name"],
                record["definition_scope"],
                record["definition_path"],
                record["subagent_name"],
                record["status"],
                record["task"],
                record["expected_output"],
                record["result_preview"],
                record["error"],
                record["started_at"],
                record["completed_at"],
                record["updated_at"],
            ),
        )
        conn.commit()
    return _subagent_invocation_to_api(record)


def update_subagent_invocation(
    invocation_id: str,
    *,
    status: str,
    result_preview: str = "",
    error: str = "",
    completed_at: str | None = None,
) -> dict[str, Any] | None:
    """更新 Subagent invocation 审计记录。"""

    ensure_db()
    now = _utc_now()
    completed_value = completed_at if completed_at is not None else (now if status in {"completed", "failed"} else None)
    with _lock, _connect() as conn:
        conn.execute(
            """
            UPDATE subagent_invocations
            SET status = ?, result_preview = ?, error = ?,
                completed_at = COALESCE(?, completed_at), updated_at = ?
            WHERE id = ?
            """,
            (status, result_preview, error, completed_value, now, invocation_id),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM subagent_invocations WHERE id = ?",
            (invocation_id,),
        ).fetchone()
    return _subagent_invocation_to_api(dict(row)) if row else None


def list_subagent_invocations(
    *,
    run_id: str | None = None,
    session_id: str | None = None,
    project_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """列出 Subagent invocation 审计记录。"""

    ensure_db()
    clauses: list[str] = []
    params: list[Any] = []
    if run_id:
        clauses.append("run_id = ?")
        params.append(run_id)
    if session_id:
        clauses.append("session_id = ?")
        params.append(session_id)
    if project_id:
        clauses.append("project_id = ?")
        params.append(project_id)
    query = "SELECT * FROM subagent_invocations"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY started_at DESC LIMIT ?"
    params.append(limit)
    with _lock, _connect() as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
    return [_subagent_invocation_to_api(dict(row)) for row in rows]


def _subagent_invocation_to_api(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "runId": row["run_id"],
        "sessionId": row["session_id"],
        "workspaceId": row.get("workspace_id", DEFAULT_WORKSPACE_ID),
        "projectId": row.get("project_id", DEFAULT_PROJECT_ID),
        "toolCallId": row["tool_call_id"],
        "parentScope": row["parent_scope"],
        "parentAgentId": row["parent_agent_id"],
        "parentAgentName": row["parent_agent_name"],
        "definitionScope": row["definition_scope"],
        "definitionPath": row["definition_path"],
        "subagentName": row["subagent_name"],
        "status": row["status"],
        "task": row["task"],
        "expectedOutput": row["expected_output"],
        "resultPreview": row["result_preview"],
        "error": row["error"],
        "startedAt": row["started_at"],
        "completedAt": row["completed_at"],
        "updatedAt": row["updated_at"],
    }


def list_run_events_by_type(
    event_type: str,
    *,
    project_id: str = DEFAULT_PROJECT_ID,
    include_payload: bool = True,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """按事件类型列出运行事件，用于 Memory 审计等横向视图。"""

    ensure_db()
    safe_limit = max(1, min(int(limit or 50), 200))
    safe_offset = max(0, int(offset or 0))
    with _lock, _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM run_events
            WHERE event_type = ? AND project_id = ?
            ORDER BY created_at DESC, sort_order DESC
            LIMIT ? OFFSET ?
            """,
            (event_type, project_id or DEFAULT_PROJECT_ID, safe_limit, safe_offset),
        ).fetchall()
    return [
        {
            "id": row["id"],
            "sessionId": row["session_id"],
            "workspaceId": row["workspace_id"],
            "projectId": row["project_id"],
            "eventType": row["event_type"],
            "scope": row["scope"],
            "agentId": row["agent_id"],
            "agentName": row["agent_name"],
            "status": row["status"],
            "title": row["title"],
            "content": row["content"],
            "payload": _loads(row["payload"], {}) if include_payload else {},
            "createdAt": row["created_at"],
            "sortOrder": row["sort_order"],
        }
        for row in rows
    ]


def count_run_events(session_id: str) -> int:
    """Count normalized runtime events for one session."""
    ensure_db()
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS total FROM run_events WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    return int(row["total"] if row else 0)


def upsert_chat_run(record: dict[str, Any]) -> dict[str, Any]:
    """Insert or update one persisted Chat Run."""
    ensure_db()
    now = _utc_now()
    created_at = record.get("createdAt") or record.get("created_at") or now
    updated_at = record.get("updatedAt") or record.get("updated_at") or now
    with _lock, _connect() as conn:
        conn.execute(
            """
            INSERT INTO chat_runs (
                id, session_id, workspace_id, project_id, agent_id, agent_name,
                task_text, mode, status, created_at, completed_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                session_id=excluded.session_id,
                workspace_id=excluded.workspace_id,
                project_id=excluded.project_id,
                agent_id=excluded.agent_id,
                agent_name=excluded.agent_name,
                task_text=excluded.task_text,
                mode=excluded.mode,
                status=excluded.status,
                completed_at=excluded.completed_at,
                updated_at=excluded.updated_at
            """,
            (
                record["id"],
                record["sessionId"],
                record.get("workspaceId", DEFAULT_WORKSPACE_ID),
                record.get("projectId", DEFAULT_PROJECT_ID),
                record.get("agentId", ""),
                record.get("agentName", ""),
                record.get("task", ""),
                record.get("mode", "agent"),
                record.get("status", "running"),
                created_at,
                record.get("completedAt"),
                updated_at,
            ),
        )
        conn.commit()
    return {**record, "createdAt": created_at, "updatedAt": updated_at}


def update_chat_run_status(run_id: str, status: str, completed_at: str | None = None) -> None:
    """Update persisted Chat Run status."""
    ensure_db()
    with _lock, _connect() as conn:
        conn.execute(
            """
            UPDATE chat_runs
            SET status = ?, completed_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, completed_at, _utc_now(), run_id),
        )
        conn.commit()


def append_chat_run_event(run_id: str, raw_event: str) -> dict[str, Any]:
    """Append one raw SSE event to a persisted Chat Run."""
    ensure_db()
    event_id = f"run-evt-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
    created_at = _utc_now()
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 AS next_order FROM chat_run_events WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        sort_order = int(row["next_order"] if row else 0)
        conn.execute(
            """
            INSERT INTO chat_run_events(id, run_id, raw_event, created_at, sort_order)
            VALUES (?, ?, ?, ?, ?)
            """,
            (event_id, run_id, raw_event, created_at, sort_order),
        )
        conn.execute(
            "UPDATE chat_runs SET updated_at = ? WHERE id = ?",
            (created_at, run_id),
        )
        conn.commit()
    return {
        "id": event_id,
        "runId": run_id,
        "rawEvent": raw_event,
        "createdAt": created_at,
        "sortOrder": sort_order,
    }


def upsert_chat_run_state(run_id: str, state: dict[str, Any]) -> dict[str, Any]:
    """保存一个 RunState 快照。"""

    ensure_db()
    updated_at = _utc_now()
    payload = {**state, "updatedAt": state.get("updatedAt") or updated_at}
    with _lock, _connect() as conn:
        conn.execute(
            """
            INSERT INTO chat_run_states(run_id, state, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                state=excluded.state,
                updated_at=excluded.updated_at
            """,
            (run_id, _dumps(payload), updated_at),
        )
        conn.commit()
    return payload


def get_chat_run_state(run_id: str) -> dict[str, Any] | None:
    """读取一个 RunState 快照。"""

    ensure_db()
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT state FROM chat_run_states WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    if not row:
        return None
    state = _loads(row["state"], {})
    return state if isinstance(state, dict) else {}


def list_chat_runs(
    limit: int = 50,
    *,
    include_events: bool = True,
    project_id: str | None = None,
) -> list[dict[str, Any]]:
    """List persisted Chat Runs, optionally omitting raw event arrays for fast summaries."""
    ensure_db()
    with _lock, _connect() as conn:
        if project_id:
            rows = conn.execute(
                "SELECT * FROM chat_runs WHERE project_id = ? ORDER BY updated_at DESC LIMIT ?",
                (project_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM chat_runs ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        if not include_events:
            run_ids = [row["id"] for row in rows]
            event_counts: dict[str, int] = {run_id: 0 for run_id in run_ids}
            if run_ids:
                placeholders = ",".join("?" for _ in run_ids)
                count_rows = conn.execute(
                    f"""
                    SELECT run_id, COUNT(*) AS total
                    FROM chat_run_events
                    WHERE run_id IN ({placeholders})
                    GROUP BY run_id
                    """,
                    run_ids,
                ).fetchall()
                for count_row in count_rows:
                    event_counts[count_row["run_id"]] = int(count_row["total"])
            return [
                _chat_run_from_row(row, [], event_count=event_counts.get(row["id"], 0))
                for row in rows
            ]
        run_ids = [row["id"] for row in rows]
        events_by_run: dict[str, list[str]] = {run_id: [] for run_id in run_ids}
        if run_ids:
            placeholders = ",".join("?" for _ in run_ids)
            event_rows = conn.execute(
                f"""
                SELECT run_id, raw_event
                FROM chat_run_events
                WHERE run_id IN ({placeholders})
                ORDER BY run_id ASC, sort_order ASC, created_at ASC
                """,
                run_ids,
            ).fetchall()
            for event_row in event_rows:
                events_by_run.setdefault(event_row["run_id"], []).append(event_row["raw_event"])
    return [_chat_run_from_row(row, events_by_run.get(row["id"], [])) for row in rows]


def get_chat_run(run_id: str, *, event_limit: int | None = None) -> dict[str, Any] | None:
    """Read one persisted Chat Run with optionally limited raw events."""
    ensure_db()
    with _lock, _connect() as conn:
        row = conn.execute("SELECT * FROM chat_runs WHERE id = ?", (run_id,)).fetchone()
        if not row:
            return None
        count_row = conn.execute(
            "SELECT COUNT(*) AS total FROM chat_run_events WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        event_count = int(count_row["total"] if count_row else 0)
        if event_limit and event_limit > 0:
            event_rows = conn.execute(
                """
                SELECT raw_event FROM (
                    SELECT raw_event, sort_order, created_at FROM chat_run_events
                    WHERE run_id = ?
                    ORDER BY sort_order DESC, created_at DESC
                    LIMIT ?
                )
                ORDER BY sort_order ASC, created_at ASC
                """,
                (run_id, event_limit),
            ).fetchall()
        else:
            event_rows = conn.execute(
                """
                SELECT raw_event FROM chat_run_events
                WHERE run_id = ?
                ORDER BY sort_order ASC, created_at ASC
                """,
                (run_id,),
            ).fetchall()
    return _chat_run_from_row(row, [event_row["raw_event"] for event_row in event_rows], event_count=event_count)


def create_workflow_run(record: dict[str, Any]) -> dict[str, Any]:
    """创建一个 Dynamic Workflow Run 及其初始节点和边。"""

    ensure_db()
    now = _utc_now()
    run_id = record.get("id") or f"workflow-{uuid.uuid4().hex}"
    nodes = list(record.get("nodes") or [])
    edges = list(record.get("edges") or [])
    with _lock, _connect() as conn:
        conn.execute(
            """
            INSERT INTO workflow_runs (
                id, schema_version, title, goal, status, current_node_id,
                created_at, updated_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                record.get("schemaVersion") or record.get("schema_version") or "workflow.v1",
                record.get("title") or "未命名任务流",
                record.get("goal") or "",
                record.get("status") or "draft",
                record.get("currentNodeId") or "",
                record.get("createdAt") or now,
                record.get("updatedAt") or now,
                record.get("completedAt"),
            ),
        )
        node_id_by_key: dict[str, str] = {}
        for index, node in enumerate(nodes):
            node_id = node.get("id") or f"workflow-node-{uuid.uuid4().hex}"
            node_key = node.get("key") or node.get("nodeKey") or node_id
            node_id_by_key[node_key] = node_id
            conn.execute(
                """
                INSERT INTO workflow_nodes (
                    id, run_id, node_key, node_type, title, instruction, status,
                    agent_id, team_id, summary, output, error, position, metadata,
                    started_at, completed_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    node_id,
                    run_id,
                    node_key,
                    node.get("type") or node.get("nodeType") or "agent",
                    node.get("title") or node_key,
                    node.get("instruction") or "",
                    node.get("status") or "pending",
                    node.get("agentId") or "",
                    node.get("teamId") or "",
                    node.get("summary") or "",
                    node.get("output") or "",
                    node.get("error") or "",
                    int(node.get("position") if node.get("position") is not None else index),
                    _dumps(node.get("metadata") or {}),
                    node.get("startedAt"),
                    node.get("completedAt"),
                    node.get("updatedAt") or now,
                ),
            )
        for edge in edges:
            from_ref = str(edge.get("from") or edge.get("fromNodeId") or "")
            to_ref = str(edge.get("to") or edge.get("toNodeId") or "")
            from_node_id = node_id_by_key.get(from_ref, from_ref)
            to_node_id = node_id_by_key.get(to_ref, to_ref)
            if not from_node_id or not to_node_id:
                continue
            conn.execute(
                """
                INSERT INTO workflow_edges (
                    id, run_id, from_node_id, to_node_id, condition, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    edge.get("id") or f"workflow-edge-{uuid.uuid4().hex}",
                    run_id,
                    from_node_id,
                    to_node_id,
                    edge.get("condition") or "",
                    edge.get("createdAt") or now,
                ),
            )
        conn.commit()
    created = get_workflow_run(run_id)
    return created or {"id": run_id}


def list_workflow_runs(limit: int = 50) -> list[dict[str, Any]]:
    """列出最近的 Dynamic Workflow Run。"""

    ensure_db()
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM workflow_runs ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        event_counts = _workflow_event_counts(conn, [row["id"] for row in rows])
    return [_workflow_run_from_row(row, event_count=event_counts.get(row["id"], 0)) for row in rows]


def get_workflow_run(run_id: str, *, event_limit: int | None = None) -> dict[str, Any] | None:
    """读取一个 Dynamic Workflow Run 的完整快照。"""

    ensure_db()
    with _lock, _connect() as conn:
        run_row = conn.execute("SELECT * FROM workflow_runs WHERE id = ?", (run_id,)).fetchone()
        if not run_row:
            return None
        node_rows = conn.execute(
            """
            SELECT * FROM workflow_nodes
            WHERE run_id = ?
            ORDER BY position ASC, updated_at ASC
            """,
            (run_id,),
        ).fetchall()
        edge_rows = conn.execute(
            """
            SELECT * FROM workflow_edges
            WHERE run_id = ?
            ORDER BY created_at ASC
            """,
            (run_id,),
        ).fetchall()
        artifact_rows = conn.execute(
            """
            SELECT * FROM workflow_artifacts
            WHERE run_id = ?
            ORDER BY created_at ASC
            """,
            (run_id,),
        ).fetchall()
        count_row = conn.execute(
            "SELECT COUNT(*) AS total FROM workflow_events WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        event_count = int(count_row["total"] if count_row else 0)
        if event_limit and event_limit > 0:
            event_rows = conn.execute(
                """
                SELECT * FROM (
                    SELECT * FROM workflow_events
                    WHERE run_id = ?
                    ORDER BY sort_order DESC, created_at DESC
                    LIMIT ?
                )
                ORDER BY sort_order ASC, created_at ASC
                """,
                (run_id, event_limit),
            ).fetchall()
        else:
            event_rows = conn.execute(
                """
                SELECT * FROM workflow_events
                WHERE run_id = ?
                ORDER BY sort_order ASC, created_at ASC
                """,
                (run_id,),
            ).fetchall()
    return {
        **_workflow_run_from_row(run_row, event_count=event_count),
        "nodes": [_workflow_node_from_row(row) for row in node_rows],
        "edges": [_workflow_edge_from_row(row) for row in edge_rows],
        "events": [_workflow_event_from_row(row) for row in event_rows],
        "artifacts": [_workflow_artifact_from_row(row) for row in artifact_rows],
    }


def update_workflow_run_status(
    run_id: str,
    status: str,
    *,
    current_node_id: str | None = None,
    completed_at: str | None = None,
) -> None:
    """更新 Dynamic Workflow Run 状态。"""

    ensure_db()
    now = _utc_now()
    with _lock, _connect() as conn:
        conn.execute(
            """
            UPDATE workflow_runs
            SET status = ?,
                current_node_id = COALESCE(?, current_node_id),
                completed_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (status, current_node_id, completed_at, now, run_id),
        )
        conn.commit()


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
    """更新 Dynamic Workflow 节点状态并返回更新后的节点。"""

    ensure_db()
    now = _utc_now()
    with _lock, _connect() as conn:
        conn.execute(
            """
            UPDATE workflow_nodes
            SET status = ?,
                summary = COALESCE(?, summary),
                output = COALESCE(?, output),
                error = COALESCE(?, error),
                started_at = COALESCE(?, started_at),
                completed_at = COALESCE(?, completed_at),
                updated_at = ?
            WHERE id = ?
            """,
            (status, summary, output, error, started_at, completed_at, now, node_id),
        )
        row = conn.execute("SELECT * FROM workflow_nodes WHERE id = ?", (node_id,)).fetchone()
        if row:
            conn.execute(
                "UPDATE workflow_runs SET current_node_id = ?, updated_at = ? WHERE id = ?",
                (node_id if status == "running" else "", now, row["run_id"]),
            )
        conn.commit()
    return _workflow_node_from_row(row) if row else None


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
    """追加一个 Dynamic Workflow 事件。"""

    ensure_db()
    now = _utc_now()
    event_id = f"workflow-event-{uuid.uuid4().hex}"
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 AS next_order FROM workflow_events WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        sort_order = int(row["next_order"] if row else 0)
        conn.execute(
            """
            INSERT INTO workflow_events (
                id, run_id, node_id, event_type, status, title,
                content, payload, created_at, sort_order
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                run_id,
                node_id,
                event_type,
                status,
                title,
                content,
                _dumps(payload or {}),
                now,
                sort_order,
            ),
        )
        conn.execute("UPDATE workflow_runs SET updated_at = ? WHERE id = ?", (now, run_id))
        conn.commit()
    return {
        "id": event_id,
        "runId": run_id,
        "nodeId": node_id,
        "type": event_type,
        "status": status,
        "title": title,
        "content": content,
        "payload": payload or {},
        "createdAt": now,
        "sortOrder": sort_order,
    }


def _workflow_event_counts(conn: sqlite3.Connection, run_ids: list[str]) -> dict[str, int]:
    if not run_ids:
        return {}
    placeholders = ",".join("?" for _ in run_ids)
    rows = conn.execute(
        f"""
        SELECT run_id, COUNT(*) AS total
        FROM workflow_events
        WHERE run_id IN ({placeholders})
        GROUP BY run_id
        """,
        run_ids,
    ).fetchall()
    return {row["run_id"]: int(row["total"]) for row in rows}


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


def _chat_run_from_row(row: sqlite3.Row, events: list[str], event_count: int | None = None) -> dict[str, Any]:
    return {
        "id": row["id"],
        "sessionId": row["session_id"],
        "workspaceId": row["workspace_id"],
        "projectId": row["project_id"],
        "agentId": row["agent_id"],
        "agentName": row["agent_name"],
        "task": row["task_text"],
        "mode": row["mode"],
        "status": row["status"],
        "createdAt": row["created_at"],
        "completedAt": row["completed_at"],
        "updatedAt": row["updated_at"],
        "events": events,
        "eventCount": len(events) if event_count is None else event_count,
    }


def _workflow_run_from_row(row: sqlite3.Row, event_count: int = 0) -> dict[str, Any]:
    return {
        "id": row["id"],
        "schemaVersion": row["schema_version"],
        "title": row["title"],
        "goal": row["goal"],
        "status": row["status"],
        "currentNodeId": row["current_node_id"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "completedAt": row["completed_at"],
        "eventCount": event_count,
    }


def _workflow_node_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "runId": row["run_id"],
        "key": row["node_key"],
        "type": row["node_type"],
        "title": row["title"],
        "instruction": row["instruction"],
        "status": row["status"],
        "agentId": row["agent_id"],
        "teamId": row["team_id"],
        "summary": row["summary"],
        "output": row["output"],
        "error": row["error"],
        "position": int(row["position"] or 0),
        "metadata": _loads(row["metadata"], {}),
        "startedAt": row["started_at"],
        "completedAt": row["completed_at"],
        "updatedAt": row["updated_at"],
    }


def _workflow_edge_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "runId": row["run_id"],
        "fromNodeId": row["from_node_id"],
        "toNodeId": row["to_node_id"],
        "condition": row["condition"],
        "createdAt": row["created_at"],
    }


def _workflow_event_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "runId": row["run_id"],
        "nodeId": row["node_id"],
        "type": row["event_type"],
        "status": row["status"],
        "title": row["title"],
        "content": row["content"],
        "payload": _loads(row["payload"], {}),
        "createdAt": row["created_at"],
        "sortOrder": int(row["sort_order"] or 0),
    }


def _workflow_artifact_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "runId": row["run_id"],
        "nodeId": row["node_id"],
        "type": row["artifact_type"],
        "title": row["title"],
        "content": row["content"],
        "uri": row["uri"],
        "metadata": _loads(row["metadata"], {}),
        "createdAt": row["created_at"],
    }


def _memory_node_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "workspace_id": row["workspace_id"],
        "project_id": row["project_id"],
        "agent_id": row["agent_id"],
        "user_id": row["user_id"],
        "node_date": row["node_date"],
        "date_range_start": row["date_range_start"],
        "date_range_end": row["date_range_end"],
        "title": row["title"],
        "summary": row["summary"],
        "conversation_digest": row["conversation_digest"],
        "search_text": row["search_text"],
        "user_inputs": _loads(row["user_inputs"], []),
        "agent_final_outputs": _loads(row["agent_final_outputs"], []),
        "tags": _loads(row["tags"], []),
        "source": row["source"],
        "source_session_id": row["source_session_id"],
        "source_run_id": row["source_run_id"],
        "source_message_id": row["source_message_id"],
        "confidence": float(row["confidence"] or 0),
        "status": row["status"],
        "pinned": bool(row["pinned"]),
        "vector_status": row["vector_status"],
        "vector_error": row["vector_error"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "last_used_at": row["last_used_at"],
        "use_count": int(row["use_count"] or 0),
    }


def upsert_memory_node(data: dict[str, Any]) -> dict[str, Any]:
    """写入或更新长期节点记忆主表。"""

    ensure_db()
    now = _utc_now()
    created_at = data.get("created_at") or now
    updated_at = data.get("updated_at") or now
    with _lock, _connect() as conn:
        conn.execute(
            """
            INSERT INTO memory_nodes (
                id, workspace_id, project_id, agent_id, user_id, node_date,
                date_range_start, date_range_end, title, summary, conversation_digest,
                search_text, user_inputs, agent_final_outputs, tags, source,
                source_session_id, source_run_id, source_message_id, confidence, status,
                pinned, vector_status, vector_error, created_at, updated_at,
                last_used_at, use_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                workspace_id=excluded.workspace_id,
                project_id=excluded.project_id,
                agent_id=excluded.agent_id,
                user_id=excluded.user_id,
                node_date=excluded.node_date,
                date_range_start=excluded.date_range_start,
                date_range_end=excluded.date_range_end,
                title=excluded.title,
                summary=excluded.summary,
                conversation_digest=excluded.conversation_digest,
                search_text=excluded.search_text,
                user_inputs=excluded.user_inputs,
                agent_final_outputs=excluded.agent_final_outputs,
                tags=excluded.tags,
                source=excluded.source,
                source_session_id=excluded.source_session_id,
                source_run_id=excluded.source_run_id,
                source_message_id=excluded.source_message_id,
                confidence=excluded.confidence,
                status=excluded.status,
                pinned=excluded.pinned,
                vector_status=excluded.vector_status,
                vector_error=excluded.vector_error,
                updated_at=excluded.updated_at
            """,
            (
                data["id"],
                data.get("workspace_id") or "default",
                data.get("project_id") or DEFAULT_PROJECT_ID,
                data["agent_id"],
                data.get("user_id") or "default",
                data["node_date"],
                data.get("date_range_start") or data["node_date"],
                data.get("date_range_end") or data["node_date"],
                data.get("title") or "未命名记忆节点",
                data.get("summary") or "",
                data.get("conversation_digest") or "",
                data.get("search_text") or "",
                _dumps(data.get("user_inputs", [])),
                _dumps(data.get("agent_final_outputs", [])),
                _dumps(data.get("tags", [])),
                data.get("source") or "chat_run",
                data.get("source_session_id") or "",
                data.get("source_run_id") or "",
                data.get("source_message_id") or "",
                float(data.get("confidence", 1)),
                data.get("status") or "active",
                1 if data.get("pinned") else 0,
                data.get("vector_status") or "pending",
                data.get("vector_error") or "",
                created_at,
                updated_at,
                data.get("last_used_at"),
                int(data.get("use_count") or 0),
            ),
        )
        row = conn.execute("SELECT * FROM memory_nodes WHERE id = ?", (data["id"],)).fetchone()
        conn.commit()
    return _memory_node_from_row(row)


def get_memory_node(node_id: str) -> dict[str, Any] | None:
    """读取单个长期节点记忆。"""

    ensure_db()
    with _lock, _connect() as conn:
        row = conn.execute("SELECT * FROM memory_nodes WHERE id = ?", (node_id,)).fetchone()
    return _memory_node_from_row(row) if row else None


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
    """按作用域和日期列出长期节点记忆。"""

    ensure_db()
    query = """
        SELECT * FROM memory_nodes
        WHERE workspace_id = ? AND project_id = ?
    """
    params: list[Any] = [workspace_id or DEFAULT_WORKSPACE_ID, project_id or DEFAULT_PROJECT_ID]
    if status:
        query += " AND status = ?"
        params.append(status)
    if agent_id:
        query += " AND agent_id = ?"
        params.append(agent_id)
    if user_id:
        query += " AND user_id = ?"
        params.append(user_id)
    if date_from:
        query += " AND node_date >= ?"
        params.append(date_from)
    if date_to:
        query += " AND node_date <= ?"
        params.append(date_to)
    query += " ORDER BY pinned DESC, node_date DESC, updated_at DESC LIMIT ?"
    params.append(max(1, min(limit, 500)))
    with _lock, _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_memory_node_from_row(row) for row in rows]


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
    """SQLite 关键词降级检索长期节点记忆。"""

    ensure_db()
    query = """
        SELECT * FROM memory_nodes
        WHERE workspace_id = ? AND project_id = ?
    """
    params: list[Any] = [workspace_id or DEFAULT_WORKSPACE_ID, project_id or DEFAULT_PROJECT_ID]
    if status:
        query += " AND status = ?"
        params.append(status)
    if agent_id:
        query += " AND agent_id = ?"
        params.append(agent_id)
    if user_id:
        query += " AND user_id = ?"
        params.append(user_id)
    if date_from:
        query += " AND node_date >= ?"
        params.append(date_from)
    if date_to:
        query += " AND node_date <= ?"
        params.append(date_to)
    if query_text.strip():
        query += " AND search_text LIKE ?"
        params.append(f"%{query_text.strip()}%")
    query += " ORDER BY pinned DESC, node_date DESC, updated_at DESC LIMIT ?"
    params.append(max(1, min(limit, 100)))
    with _lock, _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_memory_node_from_row(row) for row in rows]


def update_memory_node_vector_state(
    node_id: str,
    *,
    vector_status: str,
    vector_error: str = "",
) -> dict[str, Any] | None:
    """更新长期节点记忆的向量索引同步状态。"""

    ensure_db()
    now = _utc_now()
    with _lock, _connect() as conn:
        conn.execute(
            """
            UPDATE memory_nodes
            SET vector_status = ?, vector_error = ?, updated_at = ?
            WHERE id = ?
            """,
            (vector_status, vector_error, now, node_id),
        )
        row = conn.execute("SELECT * FROM memory_nodes WHERE id = ?", (node_id,)).fetchone()
        conn.commit()
    return _memory_node_from_row(row) if row else None


def update_memory_node_status(
    node_id: str,
    *,
    status: str,
) -> dict[str, Any] | None:
    """软更新长期节点记忆状态。"""

    ensure_db()
    now = _utc_now()
    with _lock, _connect() as conn:
        conn.execute(
            """
            UPDATE memory_nodes
            SET status = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, now, node_id),
        )
        row = conn.execute("SELECT * FROM memory_nodes WHERE id = ?", (node_id,)).fetchone()
        conn.commit()
    return _memory_node_from_row(row) if row else None


def mark_memory_node_used(node_id: str) -> dict[str, Any] | None:
    """记录长期节点记忆被查询使用。"""

    ensure_db()
    now = _utc_now()
    with _lock, _connect() as conn:
        conn.execute(
            """
            UPDATE memory_nodes
            SET use_count = use_count + 1, last_used_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (now, now, node_id),
        )
        row = conn.execute("SELECT * FROM memory_nodes WHERE id = ?", (node_id,)).fetchone()
        conn.commit()
    return _memory_node_from_row(row) if row else None


def _memory_retrieval_feedback_from_row(row: sqlite3.Row) -> dict[str, Any]:
    """把召回反馈行转换为前端和排序共用的字典。"""

    return {
        "id": row["id"],
        "auditEventId": row["audit_event_id"],
        "nodeId": row["node_id"],
        "rating": row["rating"],
        "query": row["query"],
        "agentId": row["agent_id"],
        "sessionId": row["session_id"],
        "reason": row["reason"],
        "createdAt": row["created_at"],
    }


def record_memory_retrieval_feedback(data: dict[str, Any]) -> dict[str, Any]:
    """记录用户对历史记忆召回命中的结构化反馈。"""

    ensure_db()
    now = _utc_now()
    feedback_id = data.get("id") or f"mrf_{uuid.uuid4().hex}"
    with _lock, _connect() as conn:
        conn.execute(
            """
            INSERT INTO memory_retrieval_feedback (
                id, audit_event_id, node_id, rating, query, agent_id,
                session_id, reason, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                feedback_id,
                data.get("audit_event_id") or "",
                data["node_id"],
                data["rating"],
                data.get("query") or "",
                data.get("agent_id") or "",
                data.get("session_id") or "",
                data.get("reason") or "",
                data.get("created_at") or now,
            ),
        )
        row = conn.execute(
            "SELECT * FROM memory_retrieval_feedback WHERE id = ?",
            (feedback_id,),
        ).fetchone()
        conn.commit()
    return _memory_retrieval_feedback_from_row(row)


def list_memory_retrieval_feedback(limit: int = 200) -> list[dict[str, Any]]:
    """列出最近的召回反馈记录。"""

    ensure_db()
    safe_limit = max(1, min(int(limit or 200), 1000))
    with _lock, _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM memory_retrieval_feedback
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (safe_limit,),
        ).fetchall()
    return [_memory_retrieval_feedback_from_row(row) for row in rows]


def list_memory_retrieval_feedback_scores(limit: int = 500) -> dict[str, float]:
    """按历史记忆节点聚合召回反馈分，用于轻量重排。"""

    scores: dict[str, float] = {}
    for item in reversed(list_memory_retrieval_feedback(limit=limit)):
        node_id = str(item.get("nodeId") or "")
        rating = str(item.get("rating") or "")
        if not node_id:
            continue
        delta = 0.25 if rating == "relevant" else -0.45 if rating == "irrelevant" else 0.0
        scores[node_id] = max(-1.0, min(scores.get(node_id, 0.0) + delta, 1.0))
    return scores


def _skill_evolution_record_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "workspaceId": row["workspace_id"],
        "projectId": row["project_id"],
        "agentId": row["agent_id"],
        "agentName": row["agent_name"],
        "teamId": row["team_id"],
        "userId": row["user_id"],
        "sourceSessionId": row["source_session_id"],
        "sourceRunId": row["source_run_id"],
        "sourceMessageId": row["source_message_id"],
        "routerGate": _loads(row["router_gate"], {}),
        "workflowEvidence": _loads(row["workflow_evidence"], {}),
        "reusable": bool(row["reusable"]),
        "reusableReason": row["reusable_reason"],
        "triggerDescription": row["trigger_description"],
        "inputsSchema": _loads(row["inputs_schema"], {}),
        "draftSkillName": row["draft_skill_name"],
        "draftSkillDescription": row["draft_skill_description"],
        "draftSkillContent": row["draft_skill_content"],
        "draftTags": _loads(row["draft_tags"], []),
        "granularity": row["granularity"],
        "requiresWorkflow": bool(row["requires_workflow"]),
        "suggestedSplit": _loads(row["suggested_split"], []),
        "variableFlow": _loads(row["variable_flow"], []),
        "riskNotes": row["risk_notes"],
        "confidence": float(row["confidence"] or 0),
        "status": row["status"],
        "registeredSkillId": row["registered_skill_id"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def upsert_skill_evolution_record(data: dict[str, Any]) -> dict[str, Any]:
    """写入或更新 Skill 自进化候选记录。"""

    ensure_db()
    now = _utc_now()
    created_at = data.get("created_at") or now
    updated_at = data.get("updated_at") or now
    with _lock, _connect() as conn:
        conn.execute(
            """
            INSERT INTO skill_evolution_records (
                id, workspace_id, project_id, agent_id, agent_name, team_id, user_id,
                source_session_id, source_run_id, source_message_id, router_gate,
                workflow_evidence, reusable, reusable_reason, trigger_description,
                inputs_schema, draft_skill_name, draft_skill_description, draft_skill_content,
                draft_tags, granularity, requires_workflow, suggested_split, variable_flow,
                risk_notes, confidence, status, registered_skill_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                workspace_id=excluded.workspace_id,
                project_id=excluded.project_id,
                agent_id=excluded.agent_id,
                agent_name=excluded.agent_name,
                team_id=excluded.team_id,
                user_id=excluded.user_id,
                source_session_id=excluded.source_session_id,
                source_run_id=excluded.source_run_id,
                source_message_id=excluded.source_message_id,
                router_gate=excluded.router_gate,
                workflow_evidence=excluded.workflow_evidence,
                reusable=excluded.reusable,
                reusable_reason=excluded.reusable_reason,
                trigger_description=excluded.trigger_description,
                inputs_schema=excluded.inputs_schema,
                draft_skill_name=excluded.draft_skill_name,
                draft_skill_description=excluded.draft_skill_description,
                draft_skill_content=excluded.draft_skill_content,
                draft_tags=excluded.draft_tags,
                granularity=excluded.granularity,
                requires_workflow=excluded.requires_workflow,
                suggested_split=excluded.suggested_split,
                variable_flow=excluded.variable_flow,
                risk_notes=excluded.risk_notes,
                confidence=excluded.confidence,
                status=excluded.status,
                registered_skill_id=excluded.registered_skill_id,
                updated_at=excluded.updated_at
            """,
            (
                data["id"],
                data.get("workspace_id") or "default",
                data.get("project_id") or DEFAULT_PROJECT_ID,
                data["agent_id"],
                data.get("agent_name") or "",
                data.get("team_id") or "",
                data.get("user_id") or "default",
                data.get("source_session_id") or "",
                data.get("source_run_id") or "",
                data.get("source_message_id") or "",
                _dumps(data.get("router_gate", {})),
                _dumps(data.get("workflow_evidence", {})),
                1 if data.get("reusable") else 0,
                data.get("reusable_reason") or "",
                data.get("trigger_description") or "",
                _dumps(data.get("inputs_schema", {})),
                data.get("draft_skill_name") or "",
                data.get("draft_skill_description") or "",
                data.get("draft_skill_content") or "",
                _dumps(data.get("draft_tags", [])),
                data.get("granularity") or "",
                1 if data.get("requires_workflow") else 0,
                _dumps(data.get("suggested_split", [])),
                _dumps(data.get("variable_flow", [])),
                data.get("risk_notes") or "",
                float(data.get("confidence") or 0),
                data.get("status") or "skipped",
                data.get("registered_skill_id") or "",
                created_at,
                updated_at,
            ),
        )
        row = conn.execute("SELECT * FROM skill_evolution_records WHERE id = ?", (data["id"],)).fetchone()
        conn.commit()
    return _skill_evolution_record_from_row(row)


def get_skill_evolution_record(record_id: str) -> dict[str, Any] | None:
    """读取单条 Skill 自进化候选记录。"""

    ensure_db()
    with _lock, _connect() as conn:
        row = conn.execute("SELECT * FROM skill_evolution_records WHERE id = ?", (record_id,)).fetchone()
    return _skill_evolution_record_from_row(row) if row else None


def list_skill_evolution_records(
    *,
    status: str | None = None,
    agent_id: str | None = None,
    project_id: str = DEFAULT_PROJECT_ID,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """按 Project、状态或 Agent 列出 Skill 自进化候选记录。"""

    ensure_db()
    query = "SELECT * FROM skill_evolution_records WHERE project_id = ?"
    params: list[Any] = [project_id or DEFAULT_PROJECT_ID]
    if status:
        query += " AND status = ?"
        params.append(status)
    if agent_id:
        query += " AND agent_id = ?"
        params.append(agent_id)
    query += " ORDER BY updated_at DESC LIMIT ?"
    params.append(max(1, min(limit, 200)))
    with _lock, _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_skill_evolution_record_from_row(row) for row in rows]


def mark_skill_evolution_record_registered(
    record_id: str,
    *,
    registered_skill_id: str,
) -> dict[str, Any] | None:
    """标记 Skill 自进化候选已注册为标准 Skill。"""

    ensure_db()
    now = _utc_now()
    with _lock, _connect() as conn:
        conn.execute(
            """
            UPDATE skill_evolution_records
            SET status = 'registered', registered_skill_id = ?, updated_at = ?
            WHERE id = ?
            """,
            (registered_skill_id, now, record_id),
        )
        row = conn.execute("SELECT * FROM skill_evolution_records WHERE id = ?", (record_id,)).fetchone()
        conn.commit()
    return _skill_evolution_record_from_row(row) if row else None


def update_skill_evolution_record_status(
    record_id: str,
    *,
    status: str,
    risk_notes: str | None = None,
    clear_registered_skill: bool = False,
) -> dict[str, Any] | None:
    """更新 Skill 自进化候选状态。"""

    ensure_db()
    now = _utc_now()
    assignments = ["status = ?", "updated_at = ?"]
    params: list[Any] = [status, now]
    if risk_notes is not None:
        assignments.append("risk_notes = ?")
        params.append(risk_notes)
    if clear_registered_skill:
        assignments.append("registered_skill_id = ''")
    params.append(record_id)
    with _lock, _connect() as conn:
        conn.execute(
            f"""
            UPDATE skill_evolution_records
            SET {", ".join(assignments)}
            WHERE id = ?
            """,
            params,
        )
        row = conn.execute("SELECT * FROM skill_evolution_records WHERE id = ?", (record_id,)).fetchone()
        conn.commit()
    return _skill_evolution_record_from_row(row) if row else None


def _row_to_plan(row: sqlite3.Row, steps: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "id": row["id"],
        "runId": row["run_id"],
        "sessionId": row["session_id"],
        "agentId": row["agent_id"],
        "title": row["title"],
        "goal": row["goal"],
        "status": row["status"],
        "markdown": row["markdown"],
        "currentStepId": row["current_step_id"],
        "steps": steps or [],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "completedAt": row["completed_at"],
    }


def _row_to_plan_step(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "planId": row["plan_id"],
        "stepIndex": row["step_index"],
        "title": row["title"],
        "description": row["description"],
        "status": row["status"],
        "assignedAgentId": row["assigned_agent_id"],
        "evidence": row["evidence"],
        "result": row["result"],
        "startedAt": row["started_at"],
        "completedAt": row["completed_at"],
        "updatedAt": row["updated_at"],
    }


def create_plan_record(plan: dict[str, Any], steps: list[dict[str, Any]]) -> dict[str, Any]:
    """Create one structured runtime plan and its steps."""
    ensure_db()
    with _lock, _connect() as conn:
        conn.execute(
            """
            INSERT INTO plans (
                id, run_id, session_id, agent_id, title, goal, status,
                markdown, current_step_id, created_at, updated_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                plan["id"],
                plan.get("runId", ""),
                plan["sessionId"],
                plan["agentId"],
                plan["title"],
                plan["goal"],
                plan["status"],
                plan["markdown"],
                plan.get("currentStepId"),
                plan["createdAt"],
                plan["updatedAt"],
                plan.get("completedAt"),
            ),
        )
        conn.executemany(
            """
            INSERT INTO plan_steps (
                id, plan_id, step_index, title, description, status,
                assigned_agent_id, evidence, result, started_at, completed_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    step["id"],
                    plan["id"],
                    step["stepIndex"],
                    step["title"],
                    step["description"],
                    step["status"],
                    step.get("assignedAgentId"),
                    step.get("evidence", ""),
                    step.get("result", ""),
                    step.get("startedAt"),
                    step.get("completedAt"),
                    step["updatedAt"],
                )
                for step in steps
            ],
        )
        conn.commit()
    return get_plan_record(plan["id"]) or {**plan, "steps": steps}


def get_plan_record(plan_id: str) -> dict[str, Any] | None:
    ensure_db()
    with _lock, _connect() as conn:
        plan_row = conn.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
        if not plan_row:
            return None
        step_rows = conn.execute(
            "SELECT * FROM plan_steps WHERE plan_id = ? ORDER BY step_index ASC",
            (plan_id,),
        ).fetchall()
    return _row_to_plan(plan_row, [_row_to_plan_step(row) for row in step_rows])


def list_session_plans(session_id: str) -> list[dict[str, Any]]:
    ensure_db()
    with _lock, _connect() as conn:
        plan_rows = conn.execute(
            "SELECT * FROM plans WHERE session_id = ? ORDER BY created_at ASC",
            (session_id,),
        ).fetchall()
        step_rows = conn.execute(
            """
            SELECT * FROM plan_steps
            WHERE plan_id IN (SELECT id FROM plans WHERE session_id = ?)
            ORDER BY plan_id ASC, step_index ASC
            """,
            (session_id,),
        ).fetchall()
    steps_by_plan: dict[str, list[dict[str, Any]]] = {}
    for row in step_rows:
        steps_by_plan.setdefault(row["plan_id"], []).append(_row_to_plan_step(row))
    return [_row_to_plan(row, steps_by_plan.get(row["id"], [])) for row in plan_rows]


def get_active_session_plan_record(session_id: str, statuses: list[str] | tuple[str, ...]) -> dict[str, Any] | None:
    """读取一个会话最新的未结束 Plan，用于 Plan Session 锁定。"""

    if not statuses:
        return None
    ensure_db()
    placeholders = ",".join("?" for _ in statuses)
    with _lock, _connect() as conn:
        plan_row = conn.execute(
            f"""
            SELECT * FROM plans
            WHERE session_id = ? AND status IN ({placeholders})
            ORDER BY updated_at DESC, created_at DESC
            LIMIT 1
            """,
            (session_id, *statuses),
        ).fetchone()
        if not plan_row:
            return None
        step_rows = conn.execute(
            "SELECT * FROM plan_steps WHERE plan_id = ? ORDER BY step_index ASC",
            (plan_row["id"],),
        ).fetchall()
    return _row_to_plan(plan_row, [_row_to_plan_step(row) for row in step_rows])


def update_plan_record(
    plan_id: str,
    *,
    title: str | None = None,
    status: str | None = None,
    markdown: str | None = None,
    current_step_id: str | None = None,
    completed_at: str | None = None,
) -> dict[str, Any] | None:
    ensure_db()
    existing = get_plan_record(plan_id)
    if existing is None:
        return None
    now = _utc_now()
    with _lock, _connect() as conn:
        conn.execute(
            """
            UPDATE plans
            SET title = ?, status = ?, markdown = ?, current_step_id = ?, completed_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                title if title is not None else existing["title"],
                status if status is not None else existing["status"],
                markdown if markdown is not None else existing["markdown"],
                current_step_id if current_step_id is not None else existing.get("currentStepId"),
                completed_at if completed_at is not None else existing.get("completedAt"),
                now,
                plan_id,
            ),
        )
        conn.commit()
    return get_plan_record(plan_id)


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
    ensure_db()
    now = _utc_now()
    with _lock, _connect() as conn:
        row = conn.execute("SELECT * FROM plan_steps WHERE id = ?", (step_id,)).fetchone()
        if row is None:
            return None
        conn.execute(
            """
            UPDATE plan_steps
            SET title = ?, description = ?, status = ?, evidence = ?, result = ?, started_at = ?,
                completed_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                title if title is not None else row["title"],
                description if description is not None else row["description"],
                status if status is not None else row["status"],
                evidence if evidence is not None else row["evidence"],
                result if result is not None else row["result"],
                started_at if started_at is not None else row["started_at"],
                completed_at if completed_at is not None else row["completed_at"],
                now,
                step_id,
            ),
        )
        conn.commit()
    with _lock, _connect() as conn:
        next_row = conn.execute("SELECT * FROM plan_steps WHERE id = ?", (step_id,)).fetchone()
    return _row_to_plan_step(next_row) if next_row else None


def replace_plan_steps_record(plan_id: str, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Replace all structured steps for a plan."""

    ensure_db()
    now = _utc_now()
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM plan_steps WHERE plan_id = ?", (plan_id,))
        conn.execute(
            "UPDATE plans SET current_step_id = NULL, updated_at = ? WHERE id = ?",
            (now, plan_id),
        )
        conn.executemany(
            """
            INSERT INTO plan_steps (
                id, plan_id, step_index, title, description, status,
                assigned_agent_id, evidence, result, started_at, completed_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    step["id"],
                    plan_id,
                    step["stepIndex"],
                    step["title"],
                    step.get("description", ""),
                    step.get("status", "pending"),
                    step.get("assignedAgentId"),
                    step.get("evidence", ""),
                    step.get("result", ""),
                    step.get("startedAt"),
                    step.get("completedAt"),
                    step.get("updatedAt") or now,
                )
                for step in steps
            ],
        )
        conn.commit()
    plan = get_plan_record(plan_id)
    return list((plan or {}).get("steps") or [])


def append_plan_event_record(
    plan_id: str,
    *,
    step_id: str | None,
    event_type: str,
    content: str,
    run_event_id: str | None = None,
) -> dict[str, Any]:
    ensure_db()
    event_id = f"plan-evt-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
    created_at = _utc_now()
    with _lock, _connect() as conn:
        conn.execute(
            """
            INSERT INTO plan_events(id, plan_id, step_id, run_event_id, event_type, content, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (event_id, plan_id, step_id, run_event_id, event_type, content, created_at),
        )
        conn.commit()
    return {
        "id": event_id,
        "planId": plan_id,
        "stepId": step_id,
        "runEventId": run_event_id,
        "eventType": event_type,
        "content": content,
        "createdAt": created_at,
    }


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
