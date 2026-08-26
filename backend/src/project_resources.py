"""Project 持久资源白名单解析与运行时访问校验。"""

from __future__ import annotations

from dataclasses import dataclass

from src.runtime_context import RuntimeContext
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
            or (resource_type == "skill" and get_workspace_resource("skill", resource_id) is None)
        ]

    def require_agent(self, agent_id: str) -> None:
        """拒绝运行未绑定的持久 Agent；系统 default / 群聊 Host 哨兵不属于持久资源。"""

        if agent_id in {"default", "group-studio-host"}:
            return
        _require_workspace_resource(
            workspace_id=self.workspace_id,
            resource_type="agent",
            resource_id=agent_id,
        )
        if agent_id not in self.agent_ids:
            raise ValueError(f"Agent {agent_id} 未绑定到 Project {self.project_id}。")


def resolve_project_resource_access(context: RuntimeContext) -> ProjectResourceAccess:
    """从 enabled bindings 解析本轮不可变资源白名单。"""

    enabled: dict[str, set[str]] = {resource_type: set() for resource_type in PROJECT_RESOURCE_TYPES}
    for binding in list_project_resource_bindings(context.project_id):
        resource_type = str(binding.get("resourceType") or "")
        resource_id = str(binding.get("resourceId") or "")
        if not binding.get("enabled") or resource_type not in enabled or not resource_id:
            continue
        resource = get_workspace_resource(resource_type, resource_id)
        if resource and resource["workspaceId"] == context.workspace_id:
            enabled[resource_type].add(resource_id)
    return ProjectResourceAccess(
        workspace_id=context.workspace_id,
        project_id=context.project_id,
        agent_ids=frozenset(enabled["agent"]),
        skill_ids=frozenset(enabled["skill"]),
        mcp_server_ids=frozenset(enabled["mcp"]),
        http_tool_ids=frozenset(enabled["http_tool"]),
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
    resource = get_workspace_resource(resource_type, resource_id)
    if resource is None:
        raise ValueError(f"资源不存在: {resource_type}/{resource_id}")
    if resource["workspaceId"] != workspace_id:
        raise ValueError(
            f"资源 {resource_type}/{resource_id} 属于 Workspace {resource['workspaceId']}，"
            f"不能绑定到 Workspace {workspace_id} 的 Project。",
        )
