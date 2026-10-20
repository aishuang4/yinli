"""從全局目錄、Agent 上限、Session pin 與權限模式計算 Run 候選。"""

from __future__ import annotations

from typing import Any

from src.sessions.errors import SessionError
from src.sessions.records import get_session, list_resource_grants
from src.sessions.resources import _lookup


def select_run_candidates(
    session_id: str,
    *,
    agent_id: str | None = None,
    permission_mode: str = "manual",
) -> list[dict[str, str]]:
    """候選順序固定為：全局共享、Agent 顯式清單、Session pin，最後用權限模式收窄。"""

    session = get_session(session_id)
    if session is None:
        raise SessionError("session_not_found", "Session 不存在")
    from src.catalog.loader import get_registered_agent

    resolved_agent_id = str(agent_id or session["agentId"] or "default")
    agent = get_registered_agent(resolved_agent_id)
    if agent is None:
        raise SessionError("resource_not_found", "Session Agent 不存在")
    selected: dict[tuple[str, str], dict[str, str]] = {}
    _add_global_shared(selected, agent.role)
    _add_profile(selected, agent)
    _apply_pins(selected, list_resource_grants(session_id))
    if permission_mode == "read_only":
        selected = {
            key: value
            for key, value in selected.items()
            if key[0] not in {"mcp", "http_tool"}
        }
    return [selected[key] for key in sorted(selected)]


def _add_global_shared(selected: dict[tuple[str, str], dict[str, str]], role: str) -> None:
    if role == "job":
        return
    from src.skills.service import catalog_for

    for item in catalog_for(refresh=True).candidates:
        if item.enabled and item.shared and item.state == "resolved":
            selected[("skill", item.id)] = {"resourceType": "skill", "resourceId": item.id, "source": "global"}


def _add_profile(selected: dict[tuple[str, str], dict[str, str]], agent: Any) -> None:
    for resource_type, values in (
        ("skill", agent.skills),
        ("mcp", agent.mcp_tools),
        ("http_tool", agent.knowledge_tools),
        ("knowledge", agent.knowledge_tools),
    ):
        for resource_id in values:
            record = _lookup(resource_type if resource_type != "http_tool" else "http_tool", resource_id)
            if resource_type == "knowledge":
                record = _lookup("knowledge", resource_id)
            if record and record["enabled"]:
                selected[(resource_type, resource_id)] = {
                    "resourceType": resource_type,
                    "resourceId": resource_id,
                    "source": "profile",
                }


def _apply_pins(selected: dict[tuple[str, str], dict[str, str]], grants: list[dict[str, Any]]) -> None:
    for grant in grants:
        key = (str(grant["resourceType"]), str(grant["resourceId"]))
        if not grant["enabled"]:
            selected.pop(key, None)
            continue
        record = _lookup(key[0], key[1])
        if record and record["enabled"]:
            selected[key] = {
                "resourceType": key[0],
                "resourceId": key[1],
                "source": "session",
            }
