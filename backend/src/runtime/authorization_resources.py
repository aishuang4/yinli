"""授权能力读取的公开适配层：当前 catalog/Project 的边界不依赖框架权限枚举。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict

from src.project_resources import resolve_project_resource_access
from src.runtime.agent_resources import build_runtime_guardrails, build_runtime_knowledge_tools, build_runtime_mcp_servers, scope_guardrails_to_project
from src.runtime_context import resolve_runtime_context
from src.security.authorization.boundaries import RuntimeCapabilities
from src.server.routes.agents import get_registered_agent
from src.storage.database import get_project


def _stable_resource_id(value: str) -> str:
    """Skill 正文哈希只屬於資源修訂，不能寫進 Run 能力版本。"""

    text = str(value)
    return "" if text.startswith("external-") else text


class RuntimeCapabilityReader:
    """冻结工具选择上限，但每次执行重新核对 Agent、资源、连接和 Project。"""

    def __init__(self, *, context, actor_id: str, tool_scope, denied_tool_names=frozenset(),
                 extra_filesystem_scopes=(), tool_catalog=None):
        self.context = context
        self.actor_id = actor_id
        self.tool_scope = tool_scope
        self.denied_tool_names = frozenset(denied_tool_names)
        self.extra_filesystem_scopes = tuple(extra_filesystem_scopes)
        self.tool_catalog = tool_catalog

    def snapshot(self) -> tuple[str, object, list, list]:
        """能力摘要含真实连接，密钥实值不进入普通授权元数据。"""
        current = resolve_runtime_context(project_id=self.context.project_id,
                                          session_id=self.context.session_id,
                                          run_id=self.context.run_id, agent_id=self.actor_id)
        if current.workspace_id != self.context.workspace_id or current.project_root != self.context.project_root:
            raise ValueError("运行 Project 或 Workspace 归属已变化")
        resources = resolve_project_resource_access(current)
        resources.require_agent(self.actor_id)
        config = get_registered_agent(self.actor_id)
        if config is None and self.actor_id != "default":
            raise ValueError("执行 Agent 已删除")
        guardrails = scope_guardrails_to_project(build_runtime_guardrails(config), current.project_root)
        mcp = build_runtime_mcp_servers(config, allowed_server_ids=resources.mcp_server_ids)
        knowledge = build_runtime_knowledge_tools(config, allowed_tool_ids=resources.http_tool_ids)
        # 旧审批字段不进入能力摘要，不得从 trusted/bypass 推导 Chat 开放。
        capability_fields = {name: getattr(guardrails, name) for name in (
            "filesystem_scope", "shell_blacklist", "network_enabled", "execution_backend",
            "docker_image", "persistent_sandbox", "timeout_seconds")}
        capability_fields["operationScope"] = (
            getattr(getattr(config, "guardrails", None), "operation_scope", None)
            or ("read_only" if guardrails.permission_profile.is_read_only else "normal"))
        # 这里只哈希稳定的授权边界。Project 的 updatedAt、展示字段、Skill 正文和
        # MCP/HTTP 定义正文属于资源修订，不能因为正常编辑让当前 Run 的无关工具自锁。
        stable_resources = {
            "workspaceId": resources.workspace_id,
            "projectId": resources.project_id,
            "agentIds": sorted(resources.agent_ids),
            "skillIds": sorted(_stable_resource_id(item) for item in resources.skill_ids),
            "mcpServerIds": sorted(resources.mcp_server_ids),
            "httpToolIds": sorted(resources.http_tool_ids),
            "projectSkillRefs": sorted(_stable_resource_id(item) for item in resources.project_skill_refs),
        }
        payload = {"guardrails": capability_fields, "resources": stable_resources,
                   "mcpServerIds": sorted(item.name for item in mcp),
                   "knowledgeToolIds": sorted(item.id for item in knowledge),
                   "toolScope": asdict(self.tool_scope), "deny": sorted(self.denied_tool_names),
                   "extraScopes": sorted(self.extra_filesystem_scopes)}
        version = hashlib.sha256(json.dumps(payload, sort_keys=True, default=lambda item: sorted(item),
                                            ensure_ascii=False).encode()).hexdigest()
        return version, guardrails, mcp, knowledge

    async def __call__(self) -> RuntimeCapabilities:
        """失败由边界转 deny，不能将失效资源交给模型或人工扩权。"""
        version, guardrails, _, _ = self.snapshot()
        names = await self.tool_catalog() if self.tool_catalog is not None else frozenset()
        config = get_registered_agent(self.actor_id)
        operation_scope = getattr(getattr(config, "guardrails", None), "operation_scope", None)
        readonly = operation_scope == "read_only" or guardrails.permission_profile.is_read_only
        return RuntimeCapabilities(version=version, filesystem_scope=guardrails.filesystem_scope,
                                   available_tool_names=frozenset(names), denied_tool_names=self.denied_tool_names,
                                   extra_filesystem_scopes=self.extra_filesystem_scopes,
                                   operation_scope="read_only" if readonly else "normal",
                                   network_enabled=guardrails.network_enabled,
                                   shell_enabled=self.tool_scope.allow_bash and not readonly,
                                   sandbox_required=guardrails.execution_backend == "docker")
