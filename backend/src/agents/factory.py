"""
Agent 工厂模块 — 从配置创建 AgentScope 2.0 Agent 实例

职责：
1. 根据 Agent 配置创建 DashScope 模型（主模型 + Fallback 链）
2. 注入默认工具集（Read/Write/Edit/Grep/Glob/Bash/HttpRequest/WebFetch）
3. 配置 LocalWorkspace 作为工具执行环境
4. 设置 BYPASS 权限模式（工具自动执行，无需确认）
5. 管理 Agent 实例缓存（避免重复创建）
"""

import logging
import os
import shutil
import time
from pathlib import Path
from agentscope.agent import Agent
from agentscope.agent._config import ContextConfig, InjectionConfig, ModelConfig, ReActConfig
from agentscope.mcp import HttpMCPConfig, MCPClient
from agentscope.middleware import MiddlewareBase
from agentscope.skill import LocalSkillLoader, Skill as AgentScopeSkill
from agentscope.tool import Toolkit, ToolBase
from agentscope.state import AgentState
from agentscope.permission import PermissionContext
from agentscope.permission._types import PermissionMode
from agentscope.workspace import DockerWorkspace, LocalWorkspace

from src.agents.guarded_bash import (
    GuardedBash,
    _bash_error_chunk,
    _decode_windows_shell_output,
    _resolve_windows_command_shell,
    _windows_utf8_command,
)
from src.agents.model_factory import create_model_chain
from src.agents.agentscope_usage_middleware import ProviderCacheUsageMiddleware
from src.agents.session_context import carry_over_context, settle_awaiting_tool_calls
from src.agents.runtime_types import (
    AgentGuardrails,
    AgentModelOptions,
    FallbackEventCallback,
    KnowledgeToolRuntimeSpec,
    MAX_REACT_ITERS,
    McpRuntimeSpec,
    RuntimeToolScope,
)
from src.config.runtime_settings import binding_for_model
from src.config.runtime_paths import default_project_root, sandbox_root
from src.security.permissions import ApprovalPolicy, UnifiedPermissionProfile
from src.tools.ask_user import AskUser
from src.tools.configured_http_tool import ConfiguredHttpTool

from src.tools.http_request import HttpRequest
from src.tools.knowledge_base_search import KnowledgeBaseSearch
from src.tools.timeline_memory import TimelineIngest, TimelineRetrieve
from src.tools.web_fetch import WebFetch
from src.tools.scoped_edit import ScopedEdit
from src.tools.scoped_glob import ScopedGlob
from src.tools.scoped_grep import ScopedGrep
from src.tools.scoped_read import ScopedRead
from src.tools.scoped_write import ScopedWrite

logger = logging.getLogger(__name__)

# ============================================================================
# Agent 实例缓存 — 按 runtime agent_id 缓存，避免重复创建
#
# - `_agent_cache`：runtime_agent_id -> 实例。Chat 主 Agent 的 key 由
#   runtime_builder 生成，只包含影响工具集 / 模型 / 权限的稳定配置，不含
#   system prompt 正文和 run id，因此同一 session 的多轮默认复用同一实例，
#   AgentScope 自身的 `state.context` 就是会话记忆。
# - `_session_agents`：session_context_key -> 当前 runtime_agent_id。配置变化
#   导致换实例时，把旧实例上下文搬到新实例并释放旧实例，避免实例泄漏。
# - `_agent_last_used`：LRU / 闲置淘汰依据；等待用户回应中的实例不淘汰。
# ============================================================================
_agent_cache: dict[str, Agent] = {}
_session_agents: dict[str, str] = {}
_agent_last_used: dict[str, float] = {}


def _cache_key(key: str) -> str:
    """缓存键带上租户，避免不同租户的 default Agent 共用实例。"""

    from src.tenancy.context import current_tenant

    prefix = f"{current_tenant().tenant_id}:"
    return key if key.startswith(prefix) else f"{prefix}{key}"

AGENT_CACHE_MAX_SIZE = 48
AGENT_CACHE_IDLE_SECONDS = 6 * 60 * 60
_BUSY_ATTR = "_aw_busy_run_id"

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

# 压缩摘要各字段上限（字符）。jsonschema 会严格校验 maxLength，模型超长会让压缩失败并中断运行，
# 因此上限只能放宽不能收紧；靠 compression_prompt 引导精炼。
CONTEXT_SUMMARY_FIELD_LIMIT = 2000
CONTEXT_SUMMARY_NEXT_STEPS_LIMIT = 1200
# 何时压缩 / 压缩后保留多少：窗口的 75% 触发，保留最近约 20% 的原文。
# 触发点比 AgentScope 默认 0.8 略早，是给 InjectionConfig 提前量 + 一轮工具输出留余量；
# 保留比例比默认 0.1 高，避免刚压完就丢掉正在处理的文件内容。
CONTEXT_COMPRESSION_TRIGGER_RATIO = 0.75
CONTEXT_COMPRESSION_RESERVE_RATIO = 0.2
# 单条 tool result 进入上下文的 token 上限；超过部分由 AgentScope 截断并写入 workspace。
CONTEXT_TOOL_RESULT_LIMIT = 4000


def _build_context_config() -> ContextConfig:
    """创建 AgentScope 上下文压缩配置。

    只声明 `summary_template` 会消费的五个字段；此前的 `title` / `summary` 兼容字段
    从未被模板渲染，只会让模型多花输出 tokens，改为用 additionalProperties 容忍。
    """

    summary_schema = {
        "title": "SummarySchema",
        "description": "用于生成长上下文压缩摘要的结构化模型。",
        "type": "object",
        "properties": {
            "task_overview": {
                "title": "Task Overview",
                "type": "string",
                "maxLength": CONTEXT_SUMMARY_FIELD_LIMIT,
                "description": "用户核心请求、成功标准和明确约束。",
            },
            "current_state": {
                "title": "Current State",
                "type": "string",
                "maxLength": CONTEXT_SUMMARY_FIELD_LIMIT,
                "description": "已完成事项、修改过的文件路径和关键产物。",
            },
            "important_discoveries": {
                "title": "Important Discoveries",
                "type": "string",
                "maxLength": CONTEXT_SUMMARY_FIELD_LIMIT,
                "description": "关键技术发现、约束、决策原因、错误和解决方式。",
            },
            "next_steps": {
                "title": "Next Steps",
                "type": "string",
                "maxLength": CONTEXT_SUMMARY_NEXT_STEPS_LIMIT,
                "description": "下一步行动、阻塞点和优先级。",
            },
            "context_to_preserve": {
                "title": "Context To Preserve",
                "type": "string",
                "maxLength": CONTEXT_SUMMARY_FIELD_LIMIT,
                "description": "需要保留的用户偏好、项目约定、已读过的 Skill 名称和上下文。",
            },
        },
        # 不写 required、允许额外字段：模型偶尔多返回 title / summary 之类字段时不能让整轮运行失败；
        # 缺字段会让 AgentScope 的 summary_template 渲染报错，所以由 compression_prompt 明确要求五段都填。
        "additionalProperties": True,
    }
    return ContextConfig(
        trigger_ratio=CONTEXT_COMPRESSION_TRIGGER_RATIO,
        reserve_ratio=CONTEXT_COMPRESSION_RESERVE_RATIO,
        compression_prompt=(
            "<system-hint>You are compressing the previous conversation so the "
            "agent can continue reliably. Keep every field concise, factual, "
            "and under the schema maxLength. Prefer compact Chinese bullets "
            "when the conversation is Chinese. Fill all five fields "
            "task_overview, current_state, important_discoveries, next_steps, "
            "and context_to_preserve. Use absolute file paths, identifiers and "
            "dates. Do not copy long tool outputs verbatim.</system-hint>"
        ),
        summary_schema=summary_schema,
        tool_result_limit=CONTEXT_TOOL_RESULT_LIMIT,
    )


RUNTIME_STATE_TIMEZONE = "Asia/Shanghai"


def _build_injection_config() -> InjectionConfig:
    """创建运行时状态注入配置：时间按上海时区、每半小时最多刷新一次。"""

    return InjectionConfig(
        inject_runtime_state=True,
        timezone=RUNTIME_STATE_TIMEZONE,
        # 时区由 AgentScope 另起 <timezone> 字段注入；格式里不能带 %Z，否则 strptime 回读失败会每轮重复注入。
        time_format="%Y-%m-%d %H:%M",
        time_interval=0.5,
        context_buffer_ratio=0.15,
        emit_hint_event=False,
    )




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


def _seed_catalog_profiles_readonly(host_workdir: Path, extra_filesystem_scopes: list[str]) -> None:
    """把 host catalog profiles 只读复制进持久 Docker sandbox，不回写。"""

    dest = host_workdir / ".agents" / "profiles"
    dest.mkdir(parents=True, exist_ok=True)
    for scope in extra_filesystem_scopes:
        source = Path(scope) / "profiles"
        if not source.is_dir():
            continue
        for item in source.glob("*.md"):
            target = dest / item.name
            shutil.copy2(item, target)


def _safe_workspace_dir(guardrails: AgentGuardrails | None) -> str:
    default_scope = str(default_project_root())
    scope = (guardrails.filesystem_scope if guardrails else default_scope) or default_scope
    expanded = os.path.abspath(os.path.expanduser(scope))
    if expanded == os.path.abspath(os.sep):
        return default_scope
    return expanded


def _safe_explicit_dir(path: str | None) -> str | None:
    """规范化显式目录；空值或根目录视为未设置。"""

    if not str(path or "").strip():
        return None
    expanded = os.path.abspath(os.path.expanduser(path))
    if expanded == os.path.abspath(os.sep):
        return None
    return expanded


def _resolve_write_tool_base(workspace_dir: str | None, write_scope: str) -> str:
    """Write/Edit 的 base 必须位于 scope 内。

    Team Member 的 write_scope 是 session 下的私有子目录。若仍用更宽的 session
    当 base，权限检查会判定 base 越界并抛 ValueError，直接打断整个 Member session。
    """

    scope = Path(write_scope).expanduser().resolve()
    if not str(workspace_dir or "").strip():
        return str(scope)
    workspace = Path(workspace_dir).expanduser().resolve()
    if workspace == scope or workspace.is_relative_to(scope):
        return str(workspace)
    return str(scope)


def _create_mcp_clients(
    mcp_servers: list[McpRuntimeSpec],
    guardrails: AgentGuardrails | None,
) -> list[MCPClient]:
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

        # AgentScope requires stateful stdio MCP clients to be connected before
        # Toolkit construction. Route-level Agent creation is synchronous today,
        # so stdio servers remain represented by the local default tools until
        # the factory becomes async.
    return clients


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
    workspace_dir: str | None = None,
    write_scope_dir: str | None = None,
    extra_filesystem_scopes: list[str] | None = None,
) -> Toolkit:
    """
    创建默认工具集

    每个 Agent 自动注入以下基础工具：
    - Read: 读取文件
    - Write: 写入文件
    - Edit: 编辑文件
    - Grep: 搜索文件内容
    - Glob: 按模式查找文件
    - Bash: 执行 Shell 命令
    - HttpRequest: 调用 HTTP 接口
    - WebFetch: 读取公开 / 本机 / 内网网页正文

    Returns:
        Toolkit: 包含默认工具的 Toolkit 实例
    """
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


def get_or_create_agent(
    agent_id: str = "default",
    name: str = "Assistant",
    system_prompt: str = "You are a helpful assistant.",
    model_name: str | None = None,
    fallback_names: list[str] | None = None,
    thinking_enable: bool = False,
    permission_mode: PermissionMode = PermissionMode.BYPASS,
    model_options: AgentModelOptions | None = None,
    guardrails: AgentGuardrails | None = None,
    mcp_servers: list[McpRuntimeSpec] | None = None,
    knowledge_tools: list[KnowledgeToolRuntimeSpec] | None = None,
    parent_agent_id: str | None = None,
    parent_agent_name: str | None = None,
    session_id: str = "",
    tool_scope: RuntimeToolScope | None = None,
    extra_tools: list[ToolBase] | None = None,
    include_default_tools: bool = True,
    tool_name_allowlist: set[str] | None = None,
    tool_name_denylist: set[str] | None = None,
    on_fallback_event: FallbackEventCallback | None = None,
    middlewares: list[MiddlewareBase] | None = None,
    skill_paths: list[str] | None = None,
    workspace_dir: str | None = None,
    write_scope_dir: str | None = None,
    extra_filesystem_scopes: list[str] | None = None,
    session_context_key: str | None = None,
    project_id: str = "default",
) -> Agent:
    """
    获取或创建 Agent 实例

    优先从缓存获取，不存在则创建新实例并缓存。

    Args:
        agent_id: Agent 唯一标识（缓存键）
        name: Agent 名称
        system_prompt: 系统提示词
        model_name: 主模型名称（默认使用 settings.default_model）
        fallback_names: Fallback 模型名称列表
        session_context_key: Chat 会话上下文标识；提供时，同一会话换实例会
            搬运 AgentScope 对话上下文并释放旧实例。

    Returns:
        Agent: AgentScope Agent 实例
    """
    binding = binding_for_model(model_name)
    from src.tenancy.capabilities import coerce_hosted_guardrails
    from src.tenancy.context import current_tenant

    guardrails = coerce_hosted_guardrails(guardrails)
    if current_tenant().is_hosted and "Bash 中的 /workspace 就是项目根目录" not in system_prompt:
        system_prompt = f"{system_prompt}\nBash 中的 /workspace 就是项目根目录。"
    agent_id = _cache_key(agent_id)
    if session_context_key:
        session_context_key = _cache_key(session_context_key)
    # 完整配置比较只驻留内存；Key 不进入缓存键、日志或运行记录。
    cached = _agent_cache.get(agent_id)
    if cached is not None and getattr(cached, "_model_binding", None) == binding:
        if getattr(cached, _BUSY_ATTR, None):
            # 同一实例仍在执行上一轮（前端异常并发或后台 Run 未结束）：不能共享
            # AgentState，改为创建一份带上下文快照的新实例。
            logger.warning("Agent %s 仍在运行，改用快照实例承接新一轮", agent_id)
            agent_id = f"{agent_id}:fork-{int(time.time() * 1000)}"
        else:
            _touch_agent(agent_id)
            # system prompt 正文不进缓存键；配置文件被编辑后这里同步刷新。
            if getattr(cached, "_system_prompt", None) != system_prompt:
                cached._system_prompt = system_prompt
            # fallback 事件回调绑定本轮 trace recorder，复用实例时同步刷新。
            if on_fallback_event is not None and hasattr(cached.model, "on_event"):
                cached.model.on_event = on_fallback_event
            settle_awaiting_tool_calls(cached)
            return cached

    # 使用配置默认值
    primary_model = binding.primary.default_model
    fallbacks = [item.default_model for item in binding.chain[1:]]

    # 创建应用层模型链：primary -> fallback1 -> fallback2 ...
    main_model = create_model_chain(
        [primary_model, *fallbacks],
        thinking_enable=thinking_enable,
        model_options=model_options,
        on_fallback_event=on_fallback_event,
        resolved_models=binding.chain,
    )

    # 创建 Workspace — local 默认，docker 用 AgentScope 内置容器沙盒
    docker_skill_runtime_root: str | None = None
    if current_tenant().is_hosted:
        from src.sandbox.hosted_workspace import HostedDockerWorkspace, open_hosted_workspace

        workspace = open_hosted_workspace(
            tenant_id=current_tenant().tenant_id,
            project_id=project_id or "default",
            agent_id=agent_id,
            project_root=Path(workspace_dir or _safe_workspace_dir(guardrails)),
            base_image=(guardrails.docker_image if guardrails else "") or "python:3.11-slim",
            network_enabled=bool(guardrails.network_enabled) if guardrails else True,
        )
        if not isinstance(workspace, HostedDockerWorkspace):
            raise RuntimeError("托管命令只能在沙箱中执行")
        docker_skill_runtime_root = "/workspace/skills" if skill_paths else None
    elif guardrails and guardrails.execution_backend == "docker":
        if skill_paths and not guardrails.persistent_sandbox:
            raise ValueError("DockerWorkspace 使用 Skills 时必须启用 persistent_sandbox。")
        docker_skill_runtime_root = "/workspace/skills" if skill_paths else None
        workspace = DockerWorkspace(
            workspace_id=f"agent-{agent_id}".replace(":", "-")[:120],
            base_image=guardrails.docker_image or "python:3.11-slim",
            host_workdir=(
                str(sandbox_root() / agent_id.replace(":", "-").replace("/", "-"))
                if guardrails.persistent_sandbox
                else None
            ),
            skill_paths=skill_paths,
        )
        if guardrails.persistent_sandbox and extra_filesystem_scopes:
            _seed_catalog_profiles_readonly(
                Path(str(sandbox_root() / agent_id.replace(":", "-").replace("/", "-"))),
                extra_filesystem_scopes,
            )
    else:
        workspace = LocalWorkspace(workdir=workspace_dir or _safe_workspace_dir(guardrails))

    # 创建 AgentState — 设置 BYPASS 权限模式
    # BYPASS 模式：跳过所有权限检查，工具自动执行无需确认
    agent_state = AgentState(
        permission_context=PermissionContext(
            mode=permission_mode,
        ),
    )

    # 创建 Agent
    agent = Agent(
        name=name,
        system_prompt=system_prompt,
        model=main_model,
        toolkit=_create_default_toolkit(
            guardrails=guardrails,
            mcp_servers=mcp_servers,
            knowledge_tools=knowledge_tools,
            parent_agent_id=parent_agent_id or agent_id,
            parent_agent_name=parent_agent_name or name,
            session_id=session_id,
            tool_scope=tool_scope,
            extra_tools=extra_tools,
            include_default_tools=include_default_tools,
            tool_name_allowlist=tool_name_allowlist,
            tool_name_denylist=tool_name_denylist,
            skill_paths=skill_paths,
            skill_runtime_root=docker_skill_runtime_root,
            workspace_dir=workspace_dir,
            write_scope_dir=write_scope_dir,
            extra_filesystem_scopes=extra_filesystem_scopes,
        ),
        state=agent_state,
        offloader=workspace,
        middlewares=[*(middlewares or []), ProviderCacheUsageMiddleware()],
        model_config=ModelConfig(
            max_retries=3,
            fallback_model=None,
        ),
        context_config=_build_context_config(),
        react_config=ReActConfig(
            max_iters=MAX_REACT_ITERS,
        ),
        # 时间 / 上下文长度用 AgentScope 原生 HintBlock 注入：只在首轮、超过
        # time_interval 或临近压缩阈值时追加一条用户侧提示，而不是每轮改写
        # system prompt，这样供应商侧 prompt cache 才能命中。HintBlockEvent
        # 不外发，Chat SSE 契约无需新增事件。
        injection_config=_build_injection_config(),
    )

    # 缓存并返回
    agent._model_binding = binding
    _agent_cache[agent_id] = agent
    _touch_agent(agent_id)
    if session_context_key:
        _adopt_session_context(session_context_key, agent_id, agent)
    _evict_idle_agents()
    return agent


def _touch_agent(agent_id: str) -> None:
    _agent_last_used[agent_id] = time.monotonic()


def _adopt_session_context(session_context_key: str, agent_id: str, agent: Agent) -> None:
    """同一会话换实例：搬运对话上下文并释放旧实例。"""

    previous_id = _session_agents.get(session_context_key)
    _session_agents[session_context_key] = agent_id
    if not previous_id or previous_id == agent_id:
        return
    previous = _agent_cache.get(previous_id)
    if previous is None:
        return
    if getattr(previous, _BUSY_ATTR, None):
        # 旧实例仍在执行：只复制快照，不动它的状态，也不释放它。
        carry_over_context(previous, agent, settle=False)
        return
    moved = carry_over_context(previous, agent)
    logger.debug("会话 %s 换 Agent 实例，搬运 %s 条上下文", session_context_key, moved)
    _drop_agent(previous_id)


def _drop_agent(agent_id: str) -> None:
    _agent_cache.pop(agent_id, None)
    _agent_last_used.pop(agent_id, None)


def _agent_is_pinned(agent: Agent) -> bool:
    """正在执行或等待用户回应的实例不淘汰。"""

    if getattr(agent, _BUSY_ATTR, None):
        return True
    state = getattr(agent, "state", None)
    try:
        return bool(state is not None and state.has_awaiting_tool_calls(agent.name))
    except Exception:
        return False


def _evict_idle_agents(
    *,
    now: float | None = None,
    max_size: int = AGENT_CACHE_MAX_SIZE,
    idle_seconds: float = AGENT_CACHE_IDLE_SECONDS,
) -> list[str]:
    """按闲置时长和 LRU 上限淘汰实例；不被任何会话引用的旧实例优先。"""

    current = time.monotonic() if now is None else now
    active_ids = set(_session_agents.values())
    evicted: list[str] = []
    for agent_id, last_used in list(_agent_last_used.items()):
        agent = _agent_cache.get(agent_id)
        if agent is None:
            _agent_last_used.pop(agent_id, None)
            continue
        if current - last_used >= idle_seconds and not _agent_is_pinned(agent):
            evicted.append(agent_id)
    if len(_agent_cache) - len(evicted) > max_size:
        ordered = sorted(_agent_last_used.items(), key=lambda item: item[1])
        for agent_id, _ in ordered:
            if len(_agent_cache) - len(evicted) <= max_size:
                break
            if agent_id in evicted:
                continue
            agent = _agent_cache.get(agent_id)
            if agent is None or _agent_is_pinned(agent):
                continue
            # 仍是某会话当前实例的，最后才考虑
            if agent_id in active_ids and len(_agent_cache) - len(evicted) <= max_size * 2:
                continue
            evicted.append(agent_id)
    for agent_id in evicted:
        _drop_agent(agent_id)
    if evicted:
        for key, value in list(_session_agents.items()):
            if value in evicted:
                _session_agents.pop(key, None)
        logger.debug("淘汰 %s 个闲置 Agent 实例", len(evicted))
    return evicted


def mark_agent_busy(agent: Agent, run_id: str) -> None:
    """标记实例正在执行某个 Run，期间不复用、不淘汰。"""

    setattr(agent, _BUSY_ATTR, run_id or "running")


def mark_agent_idle(agent: Agent) -> None:
    """Run 结束（含暂停等待用户）后解除执行标记。"""

    if hasattr(agent, _BUSY_ATTR):
        try:
            delattr(agent, _BUSY_ATTR)
        except AttributeError:
            pass


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


def has_cached_agent(agent_id: str) -> bool:
    """Return whether a runtime Agent instance already exists in cache."""

    return _cache_key(agent_id) in _agent_cache


def invalidate_agent(agent_id: str) -> None:
    """
    清除指定 Agent 的缓存

    当 Agent 配置更新时调用，下次请求会重新创建实例。

    Args:
        agent_id: 要清除的 Agent ID
    """
    agent_id = _cache_key(agent_id)
    keys_to_remove = [
        key
        for key in _agent_cache
        if key == agent_id or key.startswith(f"{agent_id}:")
    ]
    for key in keys_to_remove:
        _drop_agent(key)
    # 会话映射保留：下一轮创建新实例时找不到旧实例，会回退到 chat_messages 重建。
    for key, value in list(_session_agents.items()):
        if value in keys_to_remove:
            _session_agents.pop(key, None)


def clear_all_agents() -> None:
    """清除所有 Agent 缓存"""
    _agent_cache.clear()
    _session_agents.clear()
    _agent_last_used.clear()
