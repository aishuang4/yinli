"""Session 資源 grant。只引用目前 tenant 的全局資源。"""

from __future__ import annotations

import json
from typing import Any

from src.sessions.errors import SessionError
from src.sessions.records import get_session, list_resource_grants

_TYPES = {"agent", "skill", "mcp", "http_tool", "knowledge"}
_OVERRIDE_TYPES = {"pinned": bool}


def get_session_resources(session_id: str) -> dict[str, Any]:
    session = get_session(session_id)
    if session is None:
        raise SessionError("session_not_found", "Session 不存在")
    return {
        "sessionId": session_id,
        "resourceGrantVersion": session["resourceGrantVersion"],
        "resources": list_resource_grants(session_id),
    }


def put_session_resources(session_id: str, resources: list[dict[str, Any]]) -> dict[str, Any]:
    """整批替換 grant。任一筆不合法則整批不寫。"""

    session = get_session(session_id)
    if session is None:
        raise SessionError("session_not_found", "Session 不存在")
    validated = [_validate_resource(session, item) for item in resources]
    from src.storage import database

    database.ensure_db()
    now = database._utc_now()
    with database._lock, database._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT resource_grant_version FROM chat_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        version = int(row["resource_grant_version"] or 0) + 1
        conn.execute(
            "UPDATE chat_sessions SET resource_grant_version = ?, updated_at = ? WHERE id = ?",
            (version, now, session_id),
        )
        conn.execute("DELETE FROM session_resource_grants WHERE session_id = ?", (session_id,))
        for item in validated:
            conn.execute(
                """
                INSERT INTO session_resource_grants (
                    session_id, resource_type, resource_id, enabled, config_override,
                    grant_version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    item["resourceType"],
                    item["resourceId"],
                    1 if item["enabled"] else 0,
                    json.dumps(item["configOverride"], ensure_ascii=False, sort_keys=True),
                    version,
                    now,
                    now,
                ),
            )
        conn.commit()
    return get_session_resources(session_id)


def _validate_resource(session: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    resource_type = str(item.get("resourceType") or item.get("resource_type") or "")
    resource_id = str(item.get("resourceId") or item.get("resource_id") or "")
    if resource_type not in _TYPES:
        raise SessionError("invalid_resource_type", "不支援的資源類型")
    _reject_cross_tenant_id(resource_id)
    override = item.get("configOverride", item.get("config_override", {}))
    if not isinstance(override, dict):
        raise SessionError("invalid_override", "configOverride 必須是物件")
    for key, value in override.items():
        expected = _OVERRIDE_TYPES.get(key)
        if expected is None or not isinstance(value, expected):
            raise SessionError("invalid_override", "configOverride 含不允許的欄位")
        if key == "sourcePath":
            _reject_cross_tenant_id(str(value))
    record = _lookup(resource_type, resource_id)
    if record is None:
        raise SessionError("resource_not_found", "全局資源不存在")
    if not record["enabled"]:
        raise SessionError("resource_disabled", "全局資源已停用")
    if not _agent_allows(session, resource_type, resource_id, record, bool(override.get("pinned"))):
        raise SessionError("agent_ineligible", "目前 Agent 不能使用該資源")
    return {
        "resourceType": resource_type,
        "resourceId": resource_id,
        "enabled": bool(item.get("enabled", True)),
        "configOverride": override,
    }


def _reject_cross_tenant_id(value: str) -> None:
    from src.tenancy.context import current_tenant

    text = str(value or "")
    if not text or "/" in text or ".." in text or text.startswith("secret:"):
        if text.startswith("secret:"):
            return
        if "/" in text or ".." in text:
            raise SessionError("cross_tenant_resource", "不能引用其他 tenant 的資源")
    tenant = current_tenant()
    if tenant.is_hosted and text.startswith("usr_") and not text.startswith(tenant.tenant_id):
        raise SessionError("cross_tenant_resource", "不能引用其他 tenant 的資源")


def _lookup(resource_type: str, resource_id: str) -> dict[str, Any] | None:
    if resource_type == "agent":
        from src.catalog.loader import get_registered_agent

        agent = get_registered_agent(resource_id)
        if agent is None:
            return None
        return {"enabled": True, "shared": True, "role": agent.role}
    if resource_type == "skill":
        from src.skills.service import catalog_for

        for item in catalog_for(refresh=True).candidates:
            if item.id == resource_id:
                return {"enabled": bool(item.enabled), "shared": bool(item.shared)}
        return None
    if resource_type == "mcp":
        from src.catalog.mcp_files import discover_mcp_files

        for item in discover_mcp_files():
            if item.id == resource_id:
                return {"enabled": bool(item.enabled), "shared": False}
        return None
    from src.catalog.http_files import discover_http_files

    for item in discover_http_files():
        if item.id == resource_id:
            return {"enabled": bool(item.enabled), "shared": False}
    return None


def _agent_allows(
    session: dict[str, Any],
    resource_type: str,
    resource_id: str,
    record: dict[str, Any],
    pinned: bool,
) -> bool:
    if resource_type == "agent":
        return True
    from src.catalog.loader import get_registered_agent

    agent = get_registered_agent(str(session.get("agentId") or ""))
    if agent is None:
        return False
    if resource_type == "skill":
        listed = resource_id in set(agent.skills)
        if agent.role == "job":
            return listed or pinned
        return bool(record["shared"]) or listed or pinned
    listed_ids = set(agent.mcp_tools if resource_type == "mcp" else agent.knowledge_tools)
    return resource_id in listed_ids or pinned
