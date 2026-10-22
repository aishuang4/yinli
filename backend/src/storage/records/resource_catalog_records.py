"""Agent、MCP、HTTP Tool 与 Skill 资源目录的 SQLite 存储。"""
from __future__ import annotations


import sqlite3
from collections.abc import Callable, Mapping
from typing import Any


class ResourceCatalogRecordStore:
    """封装 Agent、MCP 与 HTTP Tool 目录的读写。"""

    def __init__(
        self,
        *,
        ensure_db: Callable[[], None],
        connect: Callable[[], sqlite3.Connection],
        lock: Any,
        utc_now: Callable[[], str],
        dumps: Callable[[Any], str],
        loads: Callable[[str | None, Any], Any],
        normalize_guardrails: Callable[[dict[str, Any]], dict[str, Any]],
        default_model_config: Mapping[str, Any],
        default_plan_config: Mapping[str, Any],
        default_guardrails: Mapping[str, Any],
    ) -> None:
        self.ensure_db = ensure_db
        self.connect = connect
        self.lock = lock
        self.utc_now = utc_now
        self.dumps = dumps
        self.loads = loads
        self.normalize_guardrails = normalize_guardrails
        self.default_model_config = default_model_config
        self.default_plan_config = default_plan_config
        self.default_guardrails = default_guardrails

    def list_agents(self) -> list[dict[str, Any]]:
        self.ensure_db()
        with self.lock, self.connect() as conn:
            rows = conn.execute("SELECT * FROM agents ORDER BY created_at ASC").fetchall()
        records: list[dict[str, Any]] = []
        for row in rows:
            model_config = self.loads(row["model_config"], {})
            plan_config = self.loads(row["plan_config"], {})
            guardrails = self.loads(row["guardrails"], {})
            records.append({
                "id": row["id"],
                "name": row["name"],
                "avatar": row["avatar"],
                "description": row["description"],
                "system_prompt": row["system_prompt"],
                "modelConfig": (
                    {**self.default_model_config, **model_config}
                    if isinstance(model_config, dict)
                    else self.default_model_config
                ),
                "mcpTools": self.loads(row["mcp_tools"], []),
                "knowledgeTools": self.loads(row["knowledge_tools"], []),
                "skills": self.loads(row["skills"], []),
                "tags": self.loads(row["tags"], []),
                "planConfig": (
                    {**self.default_plan_config, **plan_config}
                    if isinstance(plan_config, dict)
                    else self.default_plan_config
                ),
                "guardrails": (
                    self.normalize_guardrails(guardrails)
                    if isinstance(guardrails, dict)
                    else self.default_guardrails
                ),
                "status": row["status"],
                "role": row["role"] if "role" in row.keys() else "chat",
                "jobToolkit": self.loads(row["job_toolkit"], {}) if "job_toolkit" in row.keys() else {},
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            })
        return records

    def upsert_agent(self, data: dict[str, Any]) -> None:
        """把一条 Agent 记录写入 `agents` 表。"""
        self.ensure_db()
        column_names = [
            "id", "name", "avatar", "description", "system_prompt", "model_config",
            "mcp_tools", "knowledge_tools", "skills", "tags", "plan_config",
            "guardrails", "status", "created_at", "updated_at",
            "role", "job_toolkit",
        ]
        update_columns = [
            "name", "avatar", "description", "system_prompt", "model_config",
            "mcp_tools", "knowledge_tools", "skills", "tags", "plan_config",
            "guardrails", "status", "updated_at",
            "role", "job_toolkit",
        ]
        values = [
            data["id"], data["name"], data.get("avatar", ""),
            data.get("description", ""), data.get("system_prompt", ""),
            self.dumps(data.get("modelConfig", {})), self.dumps(data.get("mcpTools", [])),
            self.dumps(data.get("knowledgeTools", [])), self.dumps(data.get("skills", [])),
            self.dumps(data.get("tags", [])),
            self.dumps(data.get("planConfig", self.default_plan_config)),
            self.dumps(data.get("guardrails", {})), data.get("status", "idle"),
            data.get("created_at") or self.utc_now(), data.get("updated_at") or self.utc_now(),
            data.get("role") or "chat",
            self.dumps(data.get("jobToolkit") or data.get("job_toolkit") or {}),
        ]
        with self.lock, self.connect() as conn:
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

    def replace_agents(self, records: dict[str, dict[str, Any]]) -> None:
        self.ensure_db()
        with self.lock, self.connect() as conn:
            conn.execute("DELETE FROM agents")
            conn.commit()
        for record in records.values():
            self.upsert_agent(record)

    def delete_agent(self, agent_id: str) -> None:
        self.ensure_db()
        with self.lock, self.connect() as conn:
            conn.execute("DELETE FROM agents WHERE id = ?", (agent_id,))
            conn.commit()

    def list_mcp_servers(self) -> list[dict[str, Any]]:
        self.ensure_db()
        with self.lock, self.connect() as conn:
            rows = conn.execute("SELECT * FROM mcp_servers ORDER BY updated_at ASC").fetchall()
        return [
            {
                "id": row["id"], "name": row["name"], "description": row["description"],
                "category": row["category"], "transport": row["transport"],
                "command": row["command"], "url": row["url"], "homepage": row["homepage"],
                "headers": self.loads(row["headers"], {}), "tags": self.loads(row["tags"], []),
                "tools": self.loads(row["tools"], []), "status": row["status"],
                "enabled": bool(row["enabled"]), "installedAt": row["installed_at"],
                "updatedAt": row["updated_at"],
            }
            for row in rows
        ]

    def replace_mcp_servers(self, records: dict[str, dict[str, Any]]) -> None:
        self.ensure_db()
        with self.lock, self.connect() as conn:
            conn.execute("DELETE FROM mcp_servers")
            conn.executemany(
                """
                INSERT INTO mcp_servers (
                    id, name, description, category, transport, command, url, homepage,
                    headers, tags, tools, status, enabled, installed_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        data["id"], data["name"], data.get("description", ""),
                        data.get("category", "custom"), data.get("transport", "sse"),
                        data.get("command", ""), data.get("url", ""), data.get("homepage", ""),
                        self.dumps(data.get("headers", {})), self.dumps(data.get("tags", [])),
                        self.dumps(data.get("tools", [])), data.get("status", "available"),
                        int(bool(data.get("enabled", False))), data.get("installedAt"),
                        data.get("updatedAt") or self.utc_now(),
                    )
                    for data in records.values()
                ],
            )
            conn.commit()

    def list_http_tools(self) -> list[dict[str, Any]]:
        self.ensure_db()
        with self.lock, self.connect() as conn:
            rows = conn.execute("SELECT * FROM http_tools ORDER BY updated_at DESC").fetchall()
        return [
            {
                "id": row["id"], "name": row["name"], "description": row["description"],
                "category": row["category"], "method": row["method"], "url": row["url"],
                "authType": row["auth_type"], "authConfig": self.loads(row["auth_config"], {}),
                "headers": self.loads(row["headers"], {}),
                "queryTemplate": self.loads(row["query_template"], {}),
                "bodyTemplate": self.loads(row["body_template"], {}),
                "parameterSchema": self.loads(row["parameter_schema"], {}),
                "parameterInstructions": row["parameter_instructions"],
                "responseMode": row["response_mode"], "responsePath": row["response_path"],
                "readonly": bool(row["readonly"]), "approvalPolicy": row["approval_policy"],
                "enabled": bool(row["enabled"]), "createdAt": row["created_at"],
                "updatedAt": row["updated_at"],
            }
            for row in rows
        ]

    def upsert_http_tool(self, data: dict[str, Any]) -> dict[str, Any]:
        self.ensure_db()
        now = self.utc_now()
        created_at = data.get("createdAt") or now
        updated_at = data.get("updatedAt") or now
        with self.lock, self.connect() as conn:
            conn.execute(
                """
                INSERT INTO http_tools(
                    id, name, description, category, method, url, auth_type,
                    auth_config, headers, query_template, body_template,
                    parameter_schema, parameter_instructions, response_mode,
                    response_path, readonly, approval_policy, enabled,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name, description=excluded.description,
                    category=excluded.category, method=excluded.method, url=excluded.url,
                    auth_type=excluded.auth_type, auth_config=excluded.auth_config,
                    headers=excluded.headers, query_template=excluded.query_template,
                    body_template=excluded.body_template, parameter_schema=excluded.parameter_schema,
                    parameter_instructions=excluded.parameter_instructions,
                    response_mode=excluded.response_mode, response_path=excluded.response_path,
                    readonly=excluded.readonly, approval_policy=excluded.approval_policy,
                    enabled=excluded.enabled, updated_at=excluded.updated_at
                """,
                (
                    data["id"], data["name"], data.get("description", ""),
                    data.get("category", "custom"), data.get("method", "POST").upper(), data["url"],
                    data.get("authType", "none"), self.dumps(data.get("authConfig", {})),
                    self.dumps(data.get("headers", {})), self.dumps(data.get("queryTemplate", {})),
                    self.dumps(data.get("bodyTemplate", {})),
                    self.dumps(data.get("parameterSchema", {})),
                    data.get("parameterInstructions", ""), data.get("responseMode", "full"),
                    data.get("responsePath", ""), 1 if data.get("readonly", True) else 0,
                    data.get("approvalPolicy", "inherit"), 1 if data.get("enabled", True) else 0,
                    created_at, updated_at,
                ),
            )
            conn.commit()
        return next(item for item in self.list_http_tools() if item["id"] == data["id"])

    def delete_http_tool(self, tool_id: str) -> bool:
        self.ensure_db()
        with self.lock, self.connect() as conn:
            cursor = conn.execute("DELETE FROM http_tools WHERE id = ?", (tool_id,))
            conn.commit()
        return cursor.rowcount > 0

from src.storage.engine.database import _connect, _dumps, _loads, _lock, _utc_now, ensure_db

from src.storage.config.agent_defaults import DEFAULT_MODEL_CONFIG, DEFAULT_PLAN_CONFIG, _normalize_guardrails, default_guardrails

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
        default_guardrails=default_guardrails(),
    )

def list_agents() -> list[dict[str, Any]]:
    return _resource_catalog_record_store().list_agents()

def replace_agents(records: dict[str, dict[str, Any]]) -> None:
    _resource_catalog_record_store().replace_agents(records)

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
