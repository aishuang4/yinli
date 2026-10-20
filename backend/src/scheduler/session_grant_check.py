"""Job 資源自檢只看 owner Session grant 與全局 catalog。"""

from __future__ import annotations

from src.scheduler.models import ScheduledJobStateError
from src.sessions.records import list_resource_grants


def owner_session_id(job_id: str) -> str:
    from src.storage.database import get_scheduled_job

    record = get_scheduled_job(job_id) or {}
    return str(record.get("owner_session_id") or record.get("created_from_session_id") or "")


def enabled_grant_ids(session_id: str, resource_type: str) -> set[str] | None:
    """有該類型 grant 時回傳已啟用 ID；完全沒有該類型則表示不額外收窄。"""

    if not session_id:
        return None
    typed = [
        item for item in list_resource_grants(session_id)
        if item["resourceType"] == resource_type
    ]
    if not typed:
        return None
    return {item["resourceId"] for item in typed if item["enabled"]}


def filter_inventory_by_session(items: list, session_id: str) -> list:
    """可行性只保留 Session 仍授權、且呼叫方已放進全局庫存的資源。"""

    type_by_kind = {
        "skill": "skill",
        "mcp": "mcp",
        "http": "http_tool",
        "knowledge": "knowledge",
    }
    allowed = {
        kind: enabled_grant_ids(session_id, resource_type)
        for kind, resource_type in type_by_kind.items()
    }
    kept = []
    for item in items:
        grant = allowed.get(getattr(item, "kind", ""))
        if grant is not None and getattr(item, "resource_id", "") not in grant:
            continue
        kept.append(item)
    return kept


def assert_owner_session_resources(job) -> None:
    """confirm、resume、firing 前自檢。資源停用不在這裡反向掃描 Job。"""

    job_id = str(getattr(job, "id", "") or getattr(job, "job_id", "") or "")
    session_id = owner_session_id(job_id)
    skill_ids = [str(item) for item in list(getattr(job, "skill_ids", []) or []) if str(item)]
    skill_grants = enabled_grant_ids(session_id, "skill")
    if skill_ids:
        from src.skills import SkillError, resolve_skill
        from src.skills.service import catalog_for

        catalog = catalog_for()
        for skill_id in skill_ids:
            if skill_grants is not None and skill_id not in skill_grants:
                raise ScheduledJobStateError(f"skill_unbound: Session 未授權 {skill_id}")
            try:
                resolve_skill(skill_id, catalog, require_enabled=True)
            except SkillError as exc:
                raise ScheduledJobStateError(f"{exc.code}: {exc}") from exc
    scope = getattr(job, "tool_scope", None)
    if scope is not None and getattr(scope, "allow_mcp", False):
        _require_global_tool(session_id, "mcp", "MCP 綁定已失效，不能啟用任務")
    if scope is not None and (
        getattr(scope, "allow_http", False) or getattr(scope, "allow_knowledge", False)
    ):
        _require_global_tool(session_id, "http_tool", "HTTP 工具綁定已失效，不能啟用任務")


def _require_global_tool(session_id: str, resource_type: str, message: str) -> None:
    from src.storage.database import list_http_tools, list_mcp_servers

    grants = enabled_grant_ids(session_id, resource_type)
    if resource_type == "mcp":
        usable = {
            str(item.get("id") or "")
            for item in list_mcp_servers()
            if str(item.get("status") or "") == "installed" and item.get("enabled")
        }
    else:
        usable = {str(item.get("id") or "") for item in list_http_tools() if item.get("enabled", True)}
    usable.discard("")
    if grants is not None:
        usable &= grants
    if not usable:
        raise ScheduledJobStateError(message)
