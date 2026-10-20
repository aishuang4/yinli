"""Project 持久资源白名单解析与运行时访问校验。"""

from __future__ import annotations

from dataclasses import dataclass

from src.agents.system_agents import is_system_agent_id
from src.storage.database import (
    PROJECT_RESOURCE_TYPES,
    get_workspace_resource,
    list_project_resource_bindings,
)


@dataclass(frozen=True)
class ProjectResourceAccess:
    """一次 Run 可使用的 Project 持久资源白名单。"""

    workspace_id: str
    project_id: str
    agent_ids: frozenset[str]
    skill_ids: frozenset[str]
    mcp_server_ids: frozenset[str]
    http_tool_ids: frozenset[str]
    project_skill_refs: frozenset[str] = frozenset()

    def allows(self, resource_type: str, resource_id: str) -> bool:
        """判断资源是否位于对应类型的有效白名单。"""

        resources = {
            "agent": self.agent_ids,
            "skill": self.skill_ids,
            "mcp": self.mcp_server_ids,
            "http_tool": self.http_tool_ids,
        }.get(resource_type)
        return resources is not None and resource_id in resources

    def filter_ids(self, resource_type: str, resource_ids: list[str]) -> list[str]:
        """保持 Agent 配置顺序，仅保留 Project 可用资源。"""

        return [
            resource_id
            for resource_id in resource_ids
            if self.allows(resource_type, resource_id)
        ]

    def require_agent(self, agent_id: str) -> None:
        """校验普通 Agent 属于当前 Workspace；没有 Workspace 时只要求 Agent 存在。"""

        if is_system_agent_id(agent_id) or agent_id == "group-studio-host":
            return
        if not str(self.workspace_id or "").strip():
            from src.catalog.loader import get_registered_agent

            if get_registered_agent(agent_id) is None:
                raise ValueError(f"Agent 配置不存在: {agent_id}")
            return
        _require_workspace_resource(
            workspace_id=self.workspace_id,
            resource_type="agent",
            resource_id=agent_id,
        )


def resolve_project_resource_access(context: object) -> ProjectResourceAccess:
    """从 enabled bindings 解析本轮不可变资源白名单。"""

    enabled: dict[str, set[str]] = {resource_type: set() for resource_type in PROJECT_RESOURCE_TYPES}
    from src.skills.service import catalog_for
    from src.skills.catalog import resolve_skill
    from src.skills.models import SkillError
    catalog = catalog_for()
    for binding in list_project_resource_bindings(context.project_id):
        resource_type = str(binding.get("resourceType") or "")
        resource_id = str(binding.get("resourceId") or "")
        if not binding.get("enabled") or resource_type not in enabled or not resource_id:
            continue
        if resource_type == "skill":
            try:
                candidate = resolve_skill(resource_id, catalog, require_enabled=False).candidate
                enabled["skill"].update((resource_id, candidate.id, candidate.load_name, *candidate.legacy_aliases))
            except SkillError:
                pass
            continue
        resource = get_workspace_resource(resource_type, resource_id)
        if resource and resource["workspaceId"] == context.workspace_id:
            enabled[resource_type].add(resource_id)
    from src.catalog.http_files import project_http_ids
    from src.catalog.mcp_files import project_mcp_ids
    from src.skills.access import skill_binding_ids
    enabled["skill"] = set(skill_binding_ids(catalog, list_project_resource_bindings(context.project_id)))
    project_mcp = frozenset(project_mcp_ids(context.project_root))
    project_http = frozenset(project_http_ids(context.project_root))
    return ProjectResourceAccess(
        workspace_id=context.workspace_id,
        project_id=context.project_id,
        agent_ids=frozenset(enabled["agent"]),
        skill_ids=frozenset(enabled["skill"]),
        mcp_server_ids=frozenset(enabled["mcp"] | project_mcp),
        http_tool_ids=frozenset(enabled["http_tool"] | project_http),
        project_skill_refs=frozenset(),
    )


def validate_project_resource_binding(
    *,
    workspace_id: str,
    resource_type: str,
    resource_id: str,
) -> None:
    """校验 binding 类型、资源存在性和 Workspace 归属。"""

    if resource_type not in PROJECT_RESOURCE_TYPES:
        allowed = ", ".join(PROJECT_RESOURCE_TYPES)
        raise ValueError(f"不支持的 Project resource type: {resource_type}；允许值：{allowed}")
    _require_workspace_resource(
        workspace_id=workspace_id,
        resource_type=resource_type,
        resource_id=resource_id,
    )


def _require_workspace_resource(
    *,
    workspace_id: str,
    resource_type: str,
    resource_id: str,
) -> None:
    if resource_type == "skill":
        from src.skills.service import catalog_for
        from src.skills.catalog import resolve_skill
        from src.skills.models import SkillError
        try:
            resolve_skill(resource_id, catalog_for(), require_enabled=False)
            return
        except SkillError as exc:
            raise ValueError(f"{exc.code}: {exc}") from exc
    resource = get_workspace_resource(resource_type, resource_id)
    if resource is None:
        raise ValueError(f"资源不存在: {resource_type}/{resource_id}")
    if resource["workspaceId"] != workspace_id:
        raise ValueError(
            f"资源 {resource_type}/{resource_id} 属于 Workspace {resource['workspaceId']}，"
            f"不能绑定到 Workspace {workspace_id} 的 Project。",
        )
