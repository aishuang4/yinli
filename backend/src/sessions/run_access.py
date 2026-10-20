"""Run 資源的唯一讀取入口。啟動前看候選，啟動後只讀快照。"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from src.sessions.errors import SessionError


@dataclass(frozen=True)
class RunResourceAccess:
    session_id: str
    agent_id: str
    skill_ids: frozenset[str]
    mcp_server_ids: frozenset[str]
    http_tool_ids: frozenset[str]

    def allows(self, resource_type: str, resource_id: str) -> bool:
        if resource_type == "agent":
            return resource_id == self.agent_id
        pool = {
            "skill": self.skill_ids,
            "mcp": self.mcp_server_ids,
            "http_tool": self.http_tool_ids,
            "knowledge": self.http_tool_ids,
        }.get(resource_type)
        return pool is not None and resource_id in pool

    def filter_ids(self, resource_type: str, resource_ids: Iterable[str]) -> list[str]:
        seen: set[str] = set()
        kept: list[str] = []
        for resource_id in resource_ids:
            if resource_id in seen or not self.allows(resource_type, resource_id):
                continue
            seen.add(resource_id)
            kept.append(resource_id)
        return kept


def candidate_resource_access(session_id: str, *, agent_id: str, permission_mode: str) -> RunResourceAccess:
    from src.sessions.candidates import select_run_candidates

    items = select_run_candidates(session_id, agent_id=agent_id, permission_mode=permission_mode)
    return _from_items(session_id, agent_id, items)


def run_resource_access(run_id: str) -> RunResourceAccess:
    from src.sessions.records import get_run_snapshot

    snapshot = get_run_snapshot(run_id)
    if snapshot is None:
        raise SessionError("snapshot_required", "Run 没有冻结快照")
    resources = snapshot.get("resources") or {}
    items = resources.get("items") if isinstance(resources, dict) else []
    agent_id = str((snapshot.get("agentSnapshot") or {}).get("agentId") or "")
    return _from_items(str(snapshot.get("sessionId") or ""), agent_id, items or [])


def _from_items(session_id: str, agent_id: str, items: list[dict]) -> RunResourceAccess:
    skills: list[str] = []
    mcp: list[str] = []
    http: list[str] = []
    for item in items:
        resource_type = str(item.get("resourceType") or "")
        resource_id = str(item.get("resourceId") or "")
        if resource_type == "skill":
            skills.append(resource_id)
        elif resource_type == "mcp":
            mcp.append(resource_id)
        elif resource_type in {"http_tool", "knowledge"}:
            http.append(resource_id)
    return RunResourceAccess(
        session_id=session_id,
        agent_id=agent_id,
        skill_ids=frozenset(skills),
        mcp_server_ids=frozenset(mcp),
        http_tool_ids=frozenset(http),
    )
