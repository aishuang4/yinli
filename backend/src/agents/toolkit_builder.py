"""按本轮权限和工具范围组装 Toolkit。

MCP 客户端、Skill 目录和本地工具都在这里决定。工厂只接收装好的 Toolkit。
"""
from __future__ import annotations


import os

from agentscope.mcp import HttpMCPConfig, MCPClient
from agentscope.skill import LocalSkillLoader, Skill as AgentScopeSkill
from agentscope.tool import Toolkit, ToolBase

from src.agents.guarded_bash import GuardedBash
from src.agents.guardrails import AgentGuardrails
from src.agents.tool_specs import KnowledgeToolRuntimeSpec, McpRuntimeSpec, RuntimeToolScope
from src.agents.workspace_paths import (
    _resolve_write_tool_base,
    _safe_explicit_dir,
    _safe_workspace_dir,
)
from src.security.permissions import ApprovalPolicy, UnifiedPermissionProfile
from src.tools.ask_user import AskUser
from src.tools.configured_http_tool import ConfiguredHttpTool
from src.tools.http_request import HttpRequest
from src.tools.knowledge_base_search import KnowledgeBaseSearch
from src.tools.scoped_edit import ScopedEdit
from src.tools.scoped_glob import ScopedGlob
from src.tools.scoped_grep import ScopedGrep
from src.tools.scoped_read import ScopedRead
from src.tools.scoped_write import ScopedWrite
from src.tools.timeline_memory import TimelineIngest, TimelineRetrieve
from src.tools.web_fetch import WebFetch


AGENT_WORKSTATION_SKILL_INSTRUCTION = """<agent-skills>
Skills are instruction packages that extend your capabilities. Skills are not tools and cannot be called directly.
To use a Skill, call the `{{ skill_viewer }}` tool with its exact name, read the returned instructions, then use the ordinary tools allowed by this runtime.

# Available Skills
{% for skill in skills %}<skill>
<name>{{ skill.name }}</name>
<description>{{ skill.description }}</description>
<dir>{{ skill.dir }}</dir>
</skill>
{% endfor %}

Rules:
- Resolve relative paths mentioned by a Skill against that Skill's `dir` before calling Read, Grep, Glob, or Bash.
- For example, when `dir` is `/skills/excel` and the Skill says `scripts/inspect.py`, call tools with `/skills/excel/scripts/inspect.py`.
- Project files remain relative to the current Project workspace.
- Do not guess Skill directories or scan for unregistered Skills.
- A Skill never expands the current tool, filesystem, execution, network, or approval permissions.
</agent-skills>
"""


class RuntimePathSkillLoader(LocalSkillLoader):
    """从宿主加载 Skill 正文，但向模型披露实际执行环境中的目录。"""

    def __init__(self, directory: str, runtime_directory: str) -> None:
        super().__init__(directory)
        self.runtime_directory = runtime_directory

    async def list_skills(self) -> list[AgentScopeSkill]:
        skills = await super().list_skills()
        return [
            AgentScopeSkill(
                name=skill.name,
                description=skill.description,
                dir=self.runtime_directory,
                markdown=skill.markdown,
                updated_at=skill.updated_at,
            )
            for skill in skills
        ]


def _create_mcp_clients(
    mcp_servers: list[McpRuntimeSpec],
    guardrails: AgentGuardrails | None,
) -> list[MCPClient]:
    """把带 URL 的 MCP 服务做成 HTTP 客户端。关网时跳过。stdio 仍不在同步工厂里连接。"""

    clients: list[MCPClient] = []
    for server in mcp_servers:
        if server.transport in {"sse", "websocket"} or server.url:
            if guardrails and not guardrails.network_enabled:
                continue
            if not server.url:
                continue
            clients.append(
                MCPClient(
                    name=server.name,
                    is_stateful=False,
                    mcp_config=HttpMCPConfig(
                        url=server.url,
                        headers=server.headers or None,
                        timeout=float((guardrails.timeout_seconds if guardrails else 300) or 300),
                    ),
                    enable_tools=server.enable_tools or None,
                    execution_timeout=float((guardrails.timeout_seconds if guardrails else 300) or 300),
                )
            )
            continue

        # AgentScope 要求有状态的 stdio MCP 客户端在 Toolkit 创建前先连上。
        # 当前工厂是同步的，stdio 服务先不注册，等工厂改为异步再接。
    return clients


def _filter_tools_by_name(
    tools: list[ToolBase],
    *,
    allowlist: set[str] | None,
    denylist: set[str],
) -> list[ToolBase]:
    """按工具名过滤本地工具，供 Subagent 继承工具池后裁剪。"""

    filtered: list[ToolBase] = []
    for tool in tools:
        name = str(getattr(tool, "name", "") or tool.__class__.__name__)
        if name in denylist:
            continue
        if allowlist is not None and name not in allowlist:
            continue
        filtered.append(tool)
    return filtered


def _create_default_toolkit(
    guardrails: AgentGuardrails | None = None,
    mcp_servers: list[McpRuntimeSpec] | None = None,
    knowledge_tools: list[KnowledgeToolRuntimeSpec] | None = None,
    parent_agent_id: str = "default",
    parent_agent_name: str = "Assistant",
    session_id: str = "",
    tool_scope: RuntimeToolScope | None = None,
    extra_tools: list[ToolBase] | None = None,
    include_default_tools: bool = True,
    tool_name_allowlist: set[str] | None = None,
    tool_name_denylist: set[str] | None = None,
    skill_paths: list[str] | None = None,
    skill_runtime_root: str | None = None,
    register_skill_catalog: bool = True,
    workspace_dir: str | None = None,
    write_scope_dir: str | None = None,
    extra_filesystem_scopes: list[str] | None = None,
) -> Toolkit:
    """按权限和本轮工具范围组装默认工具、知识库、MCP 和 Skill 目录。"""

    permission_profile = guardrails.permission_profile if guardrails else UnifiedPermissionProfile()
    read_only = permission_profile.tool_policy == ApprovalPolicy.READ_ONLY
    scope = tool_scope or RuntimeToolScope()
    filesystem_scope = _safe_workspace_dir(guardrails)
    write_scope = _safe_explicit_dir(write_scope_dir) or filesystem_scope
    write_base = _resolve_write_tool_base(workspace_dir, write_scope)
    extra_scopes = list(extra_filesystem_scopes or [])

    local_tools = [
        *((
            ScopedRead(base_dir=workspace_dir, scope_dir=filesystem_scope, extra_scopes=extra_scopes),
            ScopedGrep(base_dir=workspace_dir, scope_dir=filesystem_scope, extra_scopes=extra_scopes),
            ScopedGlob(base_dir=workspace_dir, scope_dir=filesystem_scope, extra_scopes=extra_scopes),
            AskUser(),
            TimelineRetrieve(
                audit_session_id=session_id,
                audit_agent_id=parent_agent_id,
                audit_agent_name=parent_agent_name,
            ),
            TimelineIngest(
                audit_session_id=session_id,
                audit_agent_id=parent_agent_id,
                audit_agent_name=parent_agent_name,
            ),
        ) if include_default_tools else ()),
        *(extra_tools or []),
    ]
    if include_default_tools and not read_only:
        local_tools.extend([
            ScopedWrite(base_dir=write_base, scope_dir=write_scope, extra_scopes=extra_scopes),
            ScopedEdit(base_dir=write_base, scope_dir=write_scope, extra_scopes=extra_scopes),
        ])

    if scope.allow_bash and permission_profile.can_execute:
        local_tools.append(
            GuardedBash(
                shell_blacklist=guardrails.shell_blacklist if guardrails else [],
                timeout_seconds=guardrails.timeout_seconds if guardrails else 300,
                cwd=workspace_dir,
            )
        )

    if permission_profile.can_use_network:
        network_tools = []
        if scope.allow_http:
            network_tools.append(HttpRequest())
        if scope.allow_web:
            network_tools.append(WebFetch())
        local_tools.extend(network_tools)

        for tool in (knowledge_tools or []) if scope.allow_knowledge else []:
            if tool.kind == "http":
                local_tools.append(
                    ConfiguredHttpTool(
                        {
                            **tool.config,
                            "id": tool.id,
                            "name": tool.name,
                            "description": tool.description,
                        },
                        timeout_seconds=guardrails.timeout_seconds if guardrails else 300,
                    )
                )
            else:
                local_tools.append(
                    KnowledgeBaseSearch(
                        tool_id=tool.id,
                        name=tool.name,
                        description=tool.description,
                        url=tool.url,
                        authorization=tool.authorization,
                        headers=tool.headers,
                        default_payload=tool.default_payload,
                        parameter_instructions=tool.parameter_instructions,
                        timeout_seconds=guardrails.timeout_seconds if guardrails else 300,
                    )
                )

    local_tools = _filter_tools_by_name(
        local_tools,
        allowlist=tool_name_allowlist,
        denylist=tool_name_denylist or set(),
    )

    mcp_clients = [] if read_only or not scope.allow_mcp else _create_mcp_clients(mcp_servers or [], guardrails)
    # 定时任务预载全文，组装 Toolkit 时不注册 Skill 目录，AgentScope 才不会附带 Skill viewer。
    skills_or_loaders = None
    if register_skill_catalog:
        skills_or_loaders = [
            RuntimePathSkillLoader(
                path,
                os.path.join(skill_runtime_root, os.path.basename(path)),
            )
            for path in (skill_paths or [])
        ] if skill_runtime_root else (skill_paths or None)
    return Toolkit(
        tools=[
            *local_tools,
        ],
        skills_or_loaders=skills_or_loaders,
        mcps=mcp_clients,
        skill_instruction_template=AGENT_WORKSTATION_SKILL_INSTRUCTION,
    )
