"""授权能力读取的公开适配层：当前 catalog/Project 的边界不依赖框架权限枚举。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict

from src.runtime.agent_resources import build_runtime_guardrails, build_runtime_knowledge_tools, build_runtime_mcp_servers
from src.security.authorization.boundaries import RuntimeCapabilities
from src.server.routes.agents import get_registered_agent


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
        """能力摘要只读本轮 Run 快照，密钥实值不进入普通授权元数据。"""

        from src.sessions.errors import SessionError
        from src.sessions.factory_snapshot import apply_run_snapshot_to_factory
        from src.sessions.run_access import run_resource_access
        from src.sessions.run_binding import frozen_resources_for_run

        run_id = str(getattr(self.context, "run_id", "") or "")
        frozen = frozen_resources_for_run(run_id)
        if frozen is None:
            raise SessionError("snapshot_required", "Run 没有冻结快照")
        from src.sessions.records import get_session

        session = get_session(str(frozen.get("sessionId") or getattr(self.context, "session_id", "") or ""))
        if session is not None and session["status"] == "archived":
            raise ValueError("Session 已归档")
        access = run_resource_access(run_id)
        config = get_registered_agent(self.actor_id)
        if config is None and self.actor_id != "default":
            raise ValueError("执行 Agent 已删除")
        applied = apply_run_snapshot_to_factory(
            frozen,
            guardrails=build_runtime_guardrails(config),
            skill_paths=[],
            mcp_servers=[],
            knowledge_tools=[],
        )
        guardrails = applied.guardrails
        mcp_config = config
        if mcp_config is not None:
            mcp = build_runtime_mcp_servers(mcp_config, allowed_server_ids=access.mcp_server_ids)
            knowledge = build_runtime_knowledge_tools(mcp_config, allowed_tool_ids=access.http_tool_ids)
        else:
            mcp = []
            knowledge = []
        resources_workspace = ""
        resources_project = ""
        resource_agent_ids = [access.agent_id] if access.agent_id else []
        resource_skills = access.skill_ids
        resource_mcp = access.mcp_server_ids
        resource_http = access.http_tool_ids
        resource_refs: set[str] = set()
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
            "workspaceId": resources_workspace,
            "projectId": resources_project,
            "agentIds": sorted(resource_agent_ids),
            "skillIds": sorted(resource_skills),
            "mcpServerIds": sorted(resource_mcp),
            "httpToolIds": sorted(resource_http),
            "projectSkillRefs": sorted(resource_refs),
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
