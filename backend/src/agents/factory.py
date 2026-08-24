"""
Agent 工厂模块 — 从配置创建 AgentScope 2.0 Agent 实例

职责：
1. 根据 Agent 配置创建 DashScope 模型（主模型 + Fallback 链）
2. 注入默认工具集（Read/Write/Edit/Grep/Glob/Bash/HttpRequest/WebFetch）
3. 配置 LocalWorkspace 作为工具执行环境
4. 设置 BYPASS 权限模式（工具自动执行，无需确认）
5. 管理 Agent 实例缓存（避免重复创建）
"""

import asyncio
import codecs
import os
import signal
import shutil
from collections.abc import AsyncGenerator, Callable, Mapping
from typing import Any

from agentscope.agent import Agent
from agentscope.agent._config import ContextConfig, InjectionConfig, ModelConfig, ReActConfig
from agentscope.mcp import HttpMCPConfig, MCPClient
from agentscope.middleware import MiddlewareBase
from agentscope.skill import LocalSkillLoader, Skill as AgentScopeSkill
from agentscope.tool import Toolkit, Bash, ToolBase, ToolChunk
from agentscope.message import TextBlock, ToolResultState
from agentscope.state import AgentState
from agentscope.permission import PermissionBehavior, PermissionContext, PermissionDecision
from agentscope.permission._types import PermissionMode
from agentscope.workspace import DockerWorkspace, LocalWorkspace

from src.agents.model_factory import create_model_chain
from src.agents.agentscope_usage_middleware import ProviderCacheUsageMiddleware
from src.agents.runtime_types import (
    AgentGuardrails,
    AgentModelOptions,
    FallbackEventCallback,
    KnowledgeToolRuntimeSpec,
    MAX_REACT_ITERS,
    McpRuntimeSpec,
    RuntimeToolScope,
    WORKSPACE_DIR,
)
from src.config.runtime_settings import get_effective_model_settings
from src.config.runtime_paths import SANDBOX_ROOT
from src.security.permissions import ApprovalPolicy, UnifiedPermissionProfile
from src.tools.ask_user import AskUser
from src.tools.configured_http_tool import ConfiguredHttpTool
from src.tools.final_answer import FinalAnswerStart
from src.tools.http_request import HttpRequest
from src.tools.knowledge_base_search import KnowledgeBaseSearch
from src.tools.memory_nodes import MemoryNodeQuery, MemoryNodeTree
from src.tools.web_fetch import WebFetch
from src.tools.scoped_edit import ScopedEdit
from src.tools.scoped_glob import ScopedGlob
from src.tools.scoped_grep import ScopedGrep
from src.tools.scoped_read import ScopedRead
from src.tools.scoped_write import ScopedWrite


# ============================================================================
# Agent 实例缓存 — 按 agent_id 缓存，避免重复创建
# ============================================================================
_agent_cache: dict[str, Agent] = {}

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

CONTEXT_SUMMARY_FIELD_LIMIT = 2000
CONTEXT_SUMMARY_NEXT_STEPS_LIMIT = 1200

def _build_context_config() -> ContextConfig:
    """创建 AgentScope 上下文压缩配置，避免默认摘要字段过短导致运行中断。"""

    summary_schema = {
        "title": "SummarySchema",
        "description": "用于生成长上下文压缩摘要的结构化模型。",
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "maxLength": CONTEXT_SUMMARY_FIELD_LIMIT,
                "description": "压缩摘要标题；AgentScope 2.0.5 可能在结构化输出中返回该字段。",
            },
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
                "description": "需要保留的用户偏好、项目约定和上下文。",
            },
            "summary": {
                "title": "Legacy Summary",
                "type": "string",
                "maxLength": CONTEXT_SUMMARY_FIELD_LIMIT * 3,
                "description": "兼容旧格式的单字段摘要。优先使用结构化字段；仅在模型无法稳定输出结构化字段时使用。",
            },
        },
        "additionalProperties": False,
    }
    return ContextConfig(
        compression_prompt=(
            "<system-hint>You are compressing the previous conversation so the "
            "agent can continue reliably. Keep every field concise, factual, "
            "and under the schema maxLength. Prefer compact Chinese bullets "
            "when the conversation is Chinese. Output the structured fields "
            "task_overview, current_state, important_discoveries, next_steps, "
            "and context_to_preserve when possible. Do not copy long tool "
            "outputs verbatim.</system-hint>"
        ),
        summary_schema=summary_schema,
        tool_result_limit=4000,
    )


class GuardedBash(Bash):
    """带提示级黑名单与 Windows 原生命令解释器支持的 Shell 工具。

    黑名单用于拦截明显误操作，不是可抵抗命令混淆的安全沙箱；强隔离依赖
    DockerWorkspace 与权限审批。
    """

    description: str = """Executes a local shell command and returns its output.

On Windows this tool uses cmd.exe directly. Use Windows command syntax there:
`dir`, `type`, `%PATH%`, `cd /d "C:\\path"`, and `&&`. Do not require Git
Bash to be installed. On macOS/Linux it uses the POSIX sh shell.
"""

    def __init__(
        self,
        shell_blacklist: list[str],
        timeout_seconds: int,
        cwd: str | None = None,
    ) -> None:
        super().__init__(cwd=cwd)
        self.shell_blacklist = [item.strip() for item in shell_blacklist if item.strip()]
        self.timeout_seconds = timeout_seconds

    async def call(  # type: ignore[override]
        self,
        command: str,
        description: str = "",
        timeout: int = 120000,
    ) -> AsyncGenerator[ToolChunk, None]:
        """在 Windows 使用 UTF-8 编码的 ``cmd.exe``，避免 PATH 或 Git Bash 依赖。"""

        if os.name != "nt" and self._backend.__class__.__name__ == "LocalBackend":
            async for chunk in self._call_local_streaming(command, timeout):
                yield chunk
            return

        if os.name != "nt":
            async for chunk in super().call(command, description, timeout):
                yield chunk
            return

        timeout_ms = min(timeout, 600000)
        shell_path = _resolve_windows_command_shell()
        if not shell_path:
            yield _bash_error_chunk(
                command,
                "未找到 Windows 命令解释器 cmd.exe。请检查 COMSPEC 和 SystemRoot 环境变量。",
            )
            return

        try:
            result = await self._backend.exec_shell(
                [shell_path, "/d", "/c", _windows_utf8_command(command)],
                cwd=self._cwd,
                timeout=timeout_ms / 1000.0,
            )
        except Exception as exc:
            detail = str(exc) or repr(exc) or exc.__class__.__name__
            yield _bash_error_chunk(
                command,
                f"无法启动 cmd.exe（{shell_path}，工作目录：{self._cwd or os.getcwd()}）：{detail}",
            )
            return

        stdout = _decode_windows_shell_output(result.stdout)
        stderr = _decode_windows_shell_output(result.stderr)
        if result.exit_code == -1 and result.stderr == b"timed out":
            yield _bash_error_chunk(command, f"命令在 {timeout_ms}ms 后超时。")
            return

        if not result.ok():
            detail = stderr or stdout or (
                f"cmd.exe 退出码为 {result.exit_code}，但未输出错误信息。"
            )
            yield _bash_error_chunk(command, detail, stdout=stdout)
            return

        output = stdout
        if stderr:
            output = f"{output}\n{stderr}" if output else stderr
        yield ToolChunk(
            content=[TextBlock(text=output[:30000])],
            state=ToolResultState.SUCCESS,
            is_last=True,
        )

    async def _call_local_streaming(
        self,
        command: str,
        timeout: int,
    ) -> AsyncGenerator[ToolChunk, None]:
        """本地 POSIX Bash 按可读 stdout 节奏返回，并在取消时终止进程组。"""

        timeout_ms = min(timeout, 600000)
        process = await asyncio.create_subprocess_exec(
            "/bin/sh",
            "-c",
            command,
            cwd=self._cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        emitted_chars = 0
        output_truncated = False
        deadline = asyncio.get_running_loop().time() + timeout_ms / 1000.0
        try:
            assert process.stdout is not None
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise asyncio.TimeoutError
                raw = await asyncio.wait_for(process.stdout.read(4096), timeout=remaining)
                if not raw:
                    break
                text = decoder.decode(raw)
                visible = text[:max(0, 30000 - emitted_chars)]
                output_truncated = output_truncated or len(visible) < len(text)
                emitted_chars += len(visible)
                if visible:
                    yield ToolChunk(
                        content=[TextBlock(text=visible)],
                        state=ToolResultState.RUNNING,
                        is_last=False,
                    )
            tail = decoder.decode(b"", final=True)
            visible_tail = tail[:max(0, 30000 - emitted_chars)]
            output_truncated = output_truncated or len(visible_tail) < len(tail)
            if visible_tail:
                yield ToolChunk(
                    content=[TextBlock(text=visible_tail)],
                    state=ToolResultState.RUNNING,
                    is_last=False,
                )
            exit_code = await process.wait()
        except asyncio.TimeoutError:
            await _terminate_posix_process_group(process)
            yield _bash_error_chunk(command, f"命令在 {timeout_ms}ms 后超时。")
            return
        except asyncio.CancelledError:
            await _terminate_posix_process_group(process)
            raise

        if exit_code != 0:
            yield _bash_error_chunk(command, f"Shell 退出码为 {exit_code}。")
            return
        final_text = "\n... (output truncated)" if output_truncated else ""
        yield ToolChunk(
            content=[TextBlock(text=final_text)],
            state=ToolResultState.SUCCESS,
            is_last=True,
        )

    async def check_permissions(self, tool_input: dict, context: PermissionContext) -> PermissionDecision:
        command = str(tool_input.get("command") or "")
        for blocked in self.shell_blacklist:
            if blocked and blocked in command:
                return PermissionDecision(
                    behavior=PermissionBehavior.DENY,
                    message=f"Command blocked by Agent guardrails: {blocked}",
                    decision_reason="Agent shell blacklist matched",
                    bypass_immune=True,
                )

        timeout_ms = int(tool_input.get("timeout") or 0)
        max_timeout_ms = max(1, self.timeout_seconds) * 1000
        if timeout_ms > max_timeout_ms:
            tool_input["timeout"] = max_timeout_ms

        return await super().check_permissions(tool_input, context)


async def _terminate_posix_process_group(process: asyncio.subprocess.Process) -> None:
    """终止 Bash 及其派生子进程，避免 Run cancel 后命令继续执行。"""

    if process.returncode is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    await process.wait()


def _resolve_windows_command_shell(
    environ: Mapping[str, str] | None = None,
    *,
    path_exists: Callable[[str], bool] = os.path.isfile,
    command_lookup: Callable[[str], str | None] = shutil.which,
) -> str | None:
    """定位 Windows ``cmd.exe``，优先使用系统给出的绝对路径。"""

    values = environ if environ is not None else os.environ
    candidates = [
        values.get("ComSpec") or values.get("COMSPEC"),
        (
            os.path.join(values["SystemRoot"], "System32", "cmd.exe")
            if values.get("SystemRoot")
            else None
        ),
    ]
    for candidate in candidates:
        if candidate and path_exists(candidate):
            return candidate
    return command_lookup("cmd.exe") or command_lookup("cmd")


def _windows_utf8_command(command: str) -> str:
    """让当前 cmd 会话使用 UTF-8，保证中文输出可由后端稳定读取。"""

    return f"chcp 65001 >nul 2>&1 & {command}"


def _decode_windows_shell_output(value: bytes) -> str:
    """读取 cmd UTF-8 输出，并兼容少数仍强制使用 GBK 的旧程序。"""

    try:
        text = value.decode("utf-8")
    except UnicodeDecodeError:
        text = value.decode("gbk", errors="replace")
    return text.replace("\r\n", "\n")


def _bash_error_chunk(command: str, error: str, *, stdout: str = "") -> ToolChunk:
    """构造统一错误结果，确保前端不会只看到空白 ``Error:``。"""

    text = f"Command failed: {command}\n"
    if stdout:
        text += f"\n\nStdout:\n{stdout}"
    text += f"\n\nError:\n{error}"
    return ToolChunk(
        content=[TextBlock(text=text[:30000])],
        state=ToolResultState.ERROR,
        is_last=True,
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


def _safe_workspace_dir(guardrails: AgentGuardrails | None) -> str:
    scope = (guardrails.filesystem_scope if guardrails else WORKSPACE_DIR) or WORKSPACE_DIR
    expanded = os.path.abspath(os.path.expanduser(scope))
    if expanded == os.path.abspath(os.sep):
        return WORKSPACE_DIR
    return expanded


def _safe_explicit_dir(path: str | None) -> str | None:
    """规范化显式目录；空值或根目录视为未设置。"""

    if not str(path or "").strip():
        return None
    expanded = os.path.abspath(os.path.expanduser(path))
    if expanded == os.path.abspath(os.sep):
        return None
    return expanded


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
    - WebFetch: 读取公开网页正文

    Returns:
        Toolkit: 包含默认工具的 Toolkit 实例
    """
    permission_profile = guardrails.permission_profile if guardrails else UnifiedPermissionProfile()
    read_only = permission_profile.tool_policy == ApprovalPolicy.READ_ONLY
    scope = tool_scope or RuntimeToolScope()
    filesystem_scope = _safe_workspace_dir(guardrails)
    write_scope = _safe_explicit_dir(write_scope_dir) or filesystem_scope

    local_tools = [
        *((
            ScopedRead(base_dir=workspace_dir, scope_dir=filesystem_scope),
            ScopedGrep(base_dir=workspace_dir, scope_dir=filesystem_scope),
            ScopedGlob(base_dir=workspace_dir, scope_dir=filesystem_scope),
            AskUser(),
            FinalAnswerStart(),
            MemoryNodeTree(
                audit_session_id=session_id,
                audit_agent_id=parent_agent_id,
                audit_agent_name=parent_agent_name,
            ),
            MemoryNodeQuery(
                audit_session_id=session_id,
                audit_agent_id=parent_agent_id,
                audit_agent_name=parent_agent_name,
            ),
        ) if include_default_tools else ()),
        *(extra_tools or []),
    ]
    if include_default_tools and not read_only:
        local_tools.extend([
            ScopedWrite(base_dir=workspace_dir, scope_dir=write_scope),
            ScopedEdit(base_dir=workspace_dir, scope_dir=write_scope),
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

    Returns:
        Agent: AgentScope Agent 实例
    """
    # 缓存命中则直接返回
    if agent_id in _agent_cache:
        return _agent_cache[agent_id]

    # 使用配置默认值
    runtime = get_effective_model_settings()
    primary_model = model_name or runtime.default_model
    fallbacks = fallback_names if fallback_names is not None else runtime.fallback_models

    # 创建应用层模型链：primary -> fallback1 -> fallback2 ...
    main_model = create_model_chain(
        [primary_model, *fallbacks],
        thinking_enable=thinking_enable,
        model_options=model_options,
        on_fallback_event=on_fallback_event,
    )

    # 创建 Workspace — local 默认，docker 用 AgentScope 内置容器沙盒
    docker_skill_runtime_root: str | None = None
    if guardrails and guardrails.execution_backend == "docker":
        if skill_paths and not guardrails.persistent_sandbox:
            raise ValueError("DockerWorkspace 使用 Skills 时必须启用 persistent_sandbox。")
        docker_skill_runtime_root = "/workspace/skills" if skill_paths else None
        workspace = DockerWorkspace(
            workspace_id=f"agent-{agent_id}".replace(":", "-")[:120],
            base_image=guardrails.docker_image or "python:3.11-slim",
            host_workdir=(
                str(SANDBOX_ROOT / agent_id.replace(":", "-").replace("/", "-"))
                if guardrails.persistent_sandbox
                else None
            ),
            skill_paths=skill_paths,
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
            max_iters=max(
                1,
                min(
                    int(model_options.max_react_iters if model_options else 40),
                    MAX_REACT_ITERS,
                ),
            ),
        ),
        # 2.0.5 默认注入时间、任务和上下文状态。升级阶段保持现有 prompt 行为，
        # 后续若采用该能力需单独完成产品语义和 HINT_BLOCK 契约设计。
        injection_config=InjectionConfig(inject_runtime_state=False),
    )

    # 缓存并返回
    _agent_cache[agent_id] = agent
    return agent


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

    return agent_id in _agent_cache


def invalidate_agent(agent_id: str) -> None:
    """
    清除指定 Agent 的缓存

    当 Agent 配置更新时调用，下次请求会重新创建实例。

    Args:
        agent_id: 要清除的 Agent ID
    """
    keys_to_remove = [
        key
        for key in _agent_cache
        if key == agent_id or key.startswith(f"{agent_id}:")
    ]
    for key in keys_to_remove:
        _agent_cache.pop(key, None)


def clear_all_agents() -> None:
    """清除所有 Agent 缓存"""
    _agent_cache.clear()
