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

from src.config.settings import settings


_lock = RLock()
_initialized = False

DEFAULT_GUARDRAILS = {
    "filesystem_scope": "/",
    "shell_blacklist": ["rm -rf /", "sudo"],
    "network_enabled": True,
    "human_approval": False,
    "tool_permission_mode": "accept_edits",
    "timeout_seconds": 300,
}

DEFAULT_PLAN_CONFIG = {
    "enabled": True,
    "mode": "structured",
    "maxSteps": 6,
    "requirePlanBeforeTools": True,
    "requireUserApproval": False,
    "allowReplanning": True,
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db_path() -> Path:
    url = settings.database_url
    prefix = "sqlite+aiosqlite:///"
    if url.startswith(prefix):
        return Path(url[len(prefix):])
    if url.startswith("sqlite:///"):
        return Path(url[len("sqlite:///"):])
    return Path("data/agent_workstation.db")


DB_PATH = _db_path()


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


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
                    callable_agents TEXT NOT NULL,
                    max_depth INTEGER NOT NULL,
                    memory_enabled INTEGER NOT NULL DEFAULT 1,
                    plan_config TEXT NOT NULL DEFAULT '{}',
                    guardrails TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS teams (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    leader_agent_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS team_members (
                    id TEXT PRIMARY KEY,
                    team_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    role TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    execution_mode TEXT NOT NULL DEFAULT 'parallel',
                    depends_on TEXT NOT NULL DEFAULT '[]',
                    handoff_prompt TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY(team_id) REFERENCES teams(id) ON DELETE CASCADE
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
                    team_id TEXT,
                    team_name TEXT,
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
                    images TEXT NOT NULL,
                    skill_calls TEXT NOT NULL,
                    team_activities TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    sort_order INTEGER NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS memory_facts (
                    id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    tags TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS memory_events (
                    id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    session_id TEXT,
                    metadata TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS memory_candidates (
                    id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    tags TEXT NOT NULL,
                    source_session_id TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(agents)").fetchall()
            }
            if "memory_enabled" not in columns:
                conn.execute(
                    "ALTER TABLE agents ADD COLUMN memory_enabled INTEGER NOT NULL DEFAULT 1"
                )
            conn.commit()
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
            member_columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(team_members)").fetchall()
            }
            if "execution_mode" not in member_columns:
                conn.execute("ALTER TABLE team_members ADD COLUMN execution_mode TEXT NOT NULL DEFAULT 'parallel'")
            if "depends_on" not in member_columns:
                conn.execute("ALTER TABLE team_members ADD COLUMN depends_on TEXT NOT NULL DEFAULT '[]'")
            if "handoff_prompt" not in member_columns:
                conn.execute("ALTER TABLE team_members ADD COLUMN handoff_prompt TEXT NOT NULL DEFAULT ''")
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
            "modelConfig": _loads(row["model_config"], {}),
            "mcpTools": _loads(row["mcp_tools"], []),
            "knowledgeTools": _loads(row["knowledge_tools"], []),
            "skills": _loads(row["skills"], []),
            "tags": _loads(row["tags"], []),
            "callableAgents": _loads(row["callable_agents"], []),
            "maxDepth": row["max_depth"],
            "memoryEnabled": bool(row["memory_enabled"]),
            "planConfig": (
                {**DEFAULT_PLAN_CONFIG, **plan_config}
                if isinstance((plan_config := _loads(row["plan_config"], {})), dict)
                else DEFAULT_PLAN_CONFIG
            ),
            "guardrails": (
                {**DEFAULT_GUARDRAILS, **guardrails}
                if isinstance((guardrails := _loads(row["guardrails"], {})), dict)
                else DEFAULT_GUARDRAILS
            ),
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        for row in rows
    ]


def upsert_agent(data: dict[str, Any]) -> None:
    ensure_db()
    with _lock, _connect() as conn:
        conn.execute(
            """
            INSERT INTO agents(
                id, name, avatar, description, system_prompt, model_config,
                mcp_tools, knowledge_tools, skills, tags, callable_agents, max_depth,
                memory_enabled, plan_config, guardrails, status, created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name, avatar=excluded.avatar,
                description=excluded.description, system_prompt=excluded.system_prompt,
                model_config=excluded.model_config, mcp_tools=excluded.mcp_tools,
                knowledge_tools=excluded.knowledge_tools,
                skills=excluded.skills, tags=excluded.tags,
                callable_agents=excluded.callable_agents, max_depth=excluded.max_depth,
                memory_enabled=excluded.memory_enabled,
                plan_config=excluded.plan_config,
                guardrails=excluded.guardrails, status=excluded.status,
                updated_at=excluded.updated_at
            """,
            (
                data["id"], data["name"], data.get("avatar", ""),
                data.get("description", ""), data.get("system_prompt", ""),
                _dumps(data.get("modelConfig", {})), _dumps(data.get("mcpTools", [])),
                _dumps(data.get("knowledgeTools", [])),
                _dumps(data.get("skills", [])), _dumps(data.get("tags", [])),
                _dumps(data.get("callableAgents", [])), data.get("maxDepth", 2),
                1 if data.get("memoryEnabled", True) else 0,
                _dumps(data.get("planConfig", DEFAULT_PLAN_CONFIG)),
                _dumps(data.get("guardrails", {})), data.get("status", "idle"),
                data.get("created_at") or _utc_now(), data.get("updated_at") or _utc_now(),
            ),
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


def list_teams() -> list[dict[str, Any]]:
    ensure_db()
    with _lock, _connect() as conn:
        teams = conn.execute("SELECT * FROM teams ORDER BY updated_at ASC").fetchall()
        members = conn.execute(
            "SELECT * FROM team_members ORDER BY team_id, sort_order ASC"
        ).fetchall()
    by_team: dict[str, list[dict[str, Any]]] = {}
    for row in members:
        by_team.setdefault(row["team_id"], []).append({
            "id": row["id"],
            "agentId": row["agent_id"],
            "name": row["name"],
            "role": row["role"],
            "description": row["description"],
            "executionMode": row["execution_mode"],
            "dependsOn": _loads(row["depends_on"], []),
            "handoffPrompt": row["handoff_prompt"],
            "status": row["status"],
        })
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "leaderAgentId": row["leader_agent_id"],
            "members": by_team.get(row["id"], []),
            "status": row["status"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }
        for row in teams
    ]


def upsert_team(data: dict[str, Any]) -> None:
    ensure_db()
    with _lock, _connect() as conn:
        conn.execute(
            """
            INSERT INTO teams VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name, description=excluded.description,
                leader_agent_id=excluded.leader_agent_id, status=excluded.status,
                updated_at=excluded.updated_at
            """,
            (
                data["id"], data["name"], data.get("description", ""),
                data["leaderAgentId"], data.get("status", "idle"),
                data["createdAt"], data["updatedAt"],
            ),
        )
        conn.execute("DELETE FROM team_members WHERE team_id = ?", (data["id"],))
        conn.executemany(
            """
            INSERT INTO team_members(
                id, team_id, agent_id, name, role, description,
                execution_mode, depends_on, handoff_prompt, status, sort_order
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    member["id"], data["id"], member["agentId"], member["name"],
                    member["role"], member.get("description", ""),
                    member.get("executionMode", "parallel"),
                    _dumps(member.get("dependsOn", [])),
                    member.get("handoffPrompt", ""),
                    member.get("status", "idle"), index,
                )
                for index, member in enumerate(data.get("members", []))
            ],
        )
        conn.commit()


def replace_teams(records: dict[str, dict[str, Any]]) -> None:
    ensure_db()
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM team_members")
        conn.execute("DELETE FROM teams")
        conn.commit()
    for record in records.values():
        upsert_team(record)


def delete_team(team_id: str) -> None:
    ensure_db()
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM team_members WHERE team_id = ?", (team_id,))
        conn.execute("DELETE FROM teams WHERE id = ?", (team_id,))
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
        }
        for row in rows
    ]


def replace_mcp_servers(records: dict[str, dict[str, Any]]) -> None:
    ensure_db()
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM mcp_servers")
        conn.executemany(
            """
            INSERT INTO mcp_servers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                )
                for data in records.values()
            ],
        )
        conn.commit()


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
        }
        for row in rows
    ]


def replace_skills(records: dict[str, dict[str, Any]]) -> None:
    ensure_db()
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM skills")
        conn.executemany(
            "INSERT INTO skills VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    data["id"], data["name"], data.get("description", ""),
                    data.get("category", "custom"), data.get("trigger", ""),
                    data.get("content", ""), _dumps(data.get("tags", [])),
                    data.get("resourcePath", ""), _dumps(data.get("resources", [])),
                    data.get("status", "available"), int(bool(data.get("enabled", False))),
                    data.get("installedAt"), data.get("updatedAt") or _utc_now(),
                )
                for data in records.values()
            ],
        )
        conn.commit()


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
            "images": _loads(row["images"], []),
            "skillCalls": _loads(row["skill_calls"], []),
            "teamActivities": _loads(row["team_activities"], []),
            "created_at": row["created_at"],
        })
    return {
        "sessions": [
            {
                "id": row["id"],
                "title": row["title"],
                "agent_id": row["agent_id"],
                "agent_name": row["agent_name"],
                "team_id": row["team_id"],
                "team_name": row["team_name"],
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
            INSERT INTO chat_sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    item["id"], item["title"], item["agent_id"], item["agent_name"],
                    item.get("team_id"), item.get("team_name"), item["created_at"],
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
                    item.get("error"), _dumps(item.get("images", [])),
                    _dumps(item.get("skillCalls", item.get("skill_calls", []))),
                    _dumps(item.get("teamActivities", item.get("team_activities", []))),
                    item["created_at"], index,
                ))
        conn.executemany(
            """
            INSERT INTO chat_messages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()


def load_agent_memory(agent_id: str) -> dict[str, Any] | None:
    ensure_db()
    with _lock, _connect() as conn:
        facts = conn.execute(
            "SELECT * FROM memory_facts WHERE agent_id = ? ORDER BY created_at ASC",
            (agent_id,),
        ).fetchall()
        events = conn.execute(
            "SELECT * FROM memory_events WHERE agent_id = ? ORDER BY created_at ASC",
            (agent_id,),
        ).fetchall()
    if not facts and not events:
        return None
    updated_values = [row["updated_at"] for row in facts] + [row["created_at"] for row in events]
    return {
        "agent_id": agent_id,
        "facts": [
            {
                "id": row["id"],
                "title": row["title"],
                "content": row["content"],
                "tags": _loads(row["tags"], []),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in facts
        ],
        "events": [
            {
                "id": row["id"],
                "type": row["type"],
                "title": row["title"],
                "content": row["content"],
                "session_id": row["session_id"],
                "metadata": _loads(row["metadata"], {}),
                "created_at": row["created_at"],
            }
            for row in events
        ],
        "updated_at": max(updated_values) if updated_values else _utc_now(),
    }


def save_agent_memory(data: dict[str, Any]) -> None:
    ensure_db()
    agent_id = data["agent_id"]
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM memory_facts WHERE agent_id = ?", (agent_id,))
        conn.execute("DELETE FROM memory_events WHERE agent_id = ?", (agent_id,))
        conn.executemany(
            "INSERT INTO memory_facts VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    item["id"], agent_id, item["title"], item["content"],
                    _dumps(item.get("tags", [])), item["created_at"], item["updated_at"],
                )
                for item in data.get("facts", [])
            ],
        )
        conn.executemany(
            "INSERT INTO memory_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    item["id"], agent_id, item["type"], item["title"], item["content"],
                    item.get("session_id"), _dumps(item.get("metadata", {})),
                    item["created_at"],
                )
                for item in data.get("events", [])
            ],
        )
        conn.commit()


def list_memory_candidates(agent_id: str, status: str | None = None) -> list[dict[str, Any]]:
    ensure_db()
    query = "SELECT * FROM memory_candidates WHERE agent_id = ?"
    params: list[Any] = [agent_id]
    if status:
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY created_at DESC"
    with _lock, _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [
        {
            "id": row["id"],
            "agent_id": row["agent_id"],
            "title": row["title"],
            "content": row["content"],
            "tags": _loads(row["tags"], []),
            "source_session_id": row["source_session_id"],
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        for row in rows
    ]


def upsert_memory_candidate(data: dict[str, Any]) -> None:
    ensure_db()
    with _lock, _connect() as conn:
        conn.execute(
            """
            INSERT INTO memory_candidates VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title=excluded.title, content=excluded.content,
                tags=excluded.tags, source_session_id=excluded.source_session_id,
                status=excluded.status, updated_at=excluded.updated_at
            """,
            (
                data["id"], data["agent_id"], data["title"], data["content"],
                _dumps(data.get("tags", [])), data.get("source_session_id"),
                data.get("status", "pending"),
                data.get("created_at") or _utc_now(),
                data.get("updated_at") or _utc_now(),
            ),
        )
        conn.commit()


def update_memory_candidate_status(agent_id: str, candidate_id: str, status: str) -> dict[str, Any] | None:
    ensure_db()
    now = _utc_now()
    with _lock, _connect() as conn:
        conn.execute(
            """
            UPDATE memory_candidates
            SET status = ?, updated_at = ?
            WHERE agent_id = ? AND id = ?
            """,
            (status, now, agent_id, candidate_id),
        )
        row = conn.execute(
            "SELECT * FROM memory_candidates WHERE agent_id = ? AND id = ?",
            (agent_id, candidate_id),
        ).fetchone()
        conn.commit()
    if row is None:
        return None
    return {
        "id": row["id"],
        "agent_id": row["agent_id"],
        "title": row["title"],
        "content": row["content"],
        "tags": _loads(row["tags"], []),
        "source_session_id": row["source_session_id"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def normalized_table_counts() -> dict[str, int]:
    ensure_db()
    tables = [
        "agents", "teams", "team_members", "mcp_servers", "skills",
        "chat_sessions", "chat_messages", "memory_facts", "memory_events",
        "memory_candidates",
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
