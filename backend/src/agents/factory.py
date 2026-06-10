"""
Agent 工厂模块 — 从配置创建 AgentScope 2.0 Agent 实例

职责：
1. 根据 Agent 配置创建 DashScope 模型（主模型 + Fallback 链）
2. 注入默认工具集（Read/Write/Edit/Grep/Glob/Bash/HttpRequest）
3. 配置 LocalWorkspace 作为工具执行环境
4. 设置 BYPASS 权限模式（工具自动执行，无需确认）
5. 管理 Agent 实例缓存（避免重复创建）
"""

import os
from dataclasses import dataclass, field
from typing import Optional

from pydantic import SecretStr

from agentscope.agent import Agent
from agentscope.agent._config import ModelConfig
from agentscope.model import (
    AnthropicChatModel,
    DashScopeChatModel,
    DeepSeekChatModel,
    GeminiChatModel,
    MoonshotChatModel,
    OllamaChatModel,
    OpenAIChatModel,
)
from agentscope.credential import DashScopeCredential
from agentscope.credential import (
    AnthropicCredential,
    DeepSeekCredential,
    GeminiCredential,
    MoonshotCredential,
    OllamaCredential,
    OpenAICredential,
)
from agentscope.mcp import HttpMCPConfig, MCPClient
from agentscope.tool import Toolkit, Bash, Read, Write, Edit, Grep, Glob
from agentscope.state import AgentState
from agentscope.permission import PermissionBehavior, PermissionContext, PermissionDecision
from agentscope.permission._types import PermissionMode
from agentscope.workspace import LocalWorkspace

from src.config.settings import settings
from src.config.runtime_settings import get_effective_model_settings
from src.tools.http_request import HttpRequest
from src.tools.knowledge_base_search import KnowledgeBaseSearch


# ============================================================================
# Workspace 目录 — Agent 工具执行的工作空间
# 默认使用用户主目录，让 Agent 能访问真实项目文件
# ============================================================================
WORKSPACE_DIR = os.path.expanduser("~")

# ============================================================================
# Agent 实例缓存 — 按 agent_id 缓存，避免重复创建
# ============================================================================
_agent_cache: dict[str, Agent] = {}


@dataclass(frozen=True)
class AgentModelOptions:
    """Runtime model parameters selected from the persisted Agent config."""

    temperature: float | None = None
    max_tokens: int | None = None


@dataclass(frozen=True)
class AgentGuardrails:
    """Runtime guardrails used while constructing the Agent."""

    filesystem_scope: str = WORKSPACE_DIR
    shell_blacklist: list[str] = field(default_factory=list)
    network_enabled: bool = True
    human_approval: bool = False
    tool_permission_mode: str = "accept_edits"
    timeout_seconds: int = 300


@dataclass(frozen=True)
class McpRuntimeSpec:
    """Serializable MCP server selection passed from route code to factory."""

    name: str
    transport: str
    url: str = ""
    command: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    enable_tools: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class KnowledgeToolRuntimeSpec:
    """Serializable knowledge base tool selection passed from route code to factory."""

    id: str
    name: str
    description: str = ""
    url: str = ""
    authorization: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    default_payload: dict[str, object] = field(default_factory=dict)
    parameter_instructions: str = ""


def _create_model(
    model_name: str,
    stream: bool = True,
    thinking_enable: bool = False,
    model_options: AgentModelOptions | None = None,
):
    """
    创建 DashScope 聊天模型实例

    Args:
        model_name: 模型名称（如 qwen-plus, qwen-max, qwen-turbo）
        stream: 是否启用流式输出

    Returns:
        DashScopeChatModel: 配置好的模型实例
    """
    parameters: dict[str, object] = {
        "thinking_enable": thinking_enable,
    }
    if model_options:
        if model_options.temperature is not None:
            parameters["temperature"] = model_options.temperature
        if model_options.max_tokens is not None:
            parameters["max_tokens"] = model_options.max_tokens

    runtime = get_effective_model_settings()
    api_key = SecretStr(runtime.api_key)
    base_url = runtime.base_url or None

    if runtime.adapter == "openai":
        return OpenAIChatModel(
            credential=OpenAICredential(api_key=api_key, base_url=base_url),
            model=model_name,
            parameters=OpenAIChatModel.Parameters(**parameters),
            stream=stream,
            max_retries=3,
        )
    if runtime.adapter == "deepseek":
        return DeepSeekChatModel(
            credential=DeepSeekCredential(api_key=api_key, base_url=runtime.base_url or "https://api.deepseek.com"),
            model=model_name,
            parameters=DeepSeekChatModel.Parameters(**parameters),
            stream=stream,
            max_retries=3,
        )
    if runtime.adapter == "moonshot":
        return MoonshotChatModel(
            credential=MoonshotCredential(api_key=api_key, base_url=runtime.base_url or "https://api.moonshot.cn/v1"),
            model=model_name,
            parameters=MoonshotChatModel.Parameters(**parameters),
            stream=stream,
            max_retries=3,
        )
    if runtime.adapter == "gemini":
        return GeminiChatModel(
            credential=GeminiCredential(api_key=api_key),
            model=model_name,
            parameters=GeminiChatModel.Parameters(**parameters),
            stream=stream,
            max_retries=3,
        )
    if runtime.adapter == "anthropic":
        anthropic_parameters = {
            key: value
            for key, value in parameters.items()
            if key in AnthropicChatModel.Parameters.model_fields
        }
        return AnthropicChatModel(
            credential=AnthropicCredential(api_key=api_key, base_url=base_url),
            model=model_name,
            parameters=AnthropicChatModel.Parameters(**anthropic_parameters),
            stream=stream,
            max_retries=3,
        )
    if runtime.adapter == "ollama":
        return OllamaChatModel(
            credential=OllamaCredential(host=runtime.base_url or None),
            model=model_name,
            parameters=OllamaChatModel.Parameters(**parameters),
            stream=stream,
            max_retries=3,
        )

    return DashScopeChatModel(
        credential=DashScopeCredential(
            api_key=api_key,
            base_url=runtime.base_url or settings.dashscope_base_url,
        ),
        model=model_name,
        parameters=DashScopeChatModel.Parameters(**parameters),
        stream=stream,
        max_retries=3,
    )


def _create_fallback_model(
    fallback_names: list[str],
    thinking_enable: bool = False,
    model_options: AgentModelOptions | None = None,
) -> Optional[DashScopeChatModel]:
    """
    创建 Fallback 模型（取第一个作为 fallback_model）

    AgentScope 2.0 的 ModelConfig 只支持单个 fallback_model，
    因此取列表第一个作为 fallback。后续可扩展为链式。

    Args:
        fallback_names: Fallback 模型名称列表（有序）

    Returns:
        最外层 Fallback 模型，如果列表为空则返回 None
    """
    if not fallback_names:
        return None

    # 取第一个 fallback 模型
    return _create_model(
        fallback_names[0],
        stream=True,
        thinking_enable=thinking_enable,
        model_options=model_options,
    )


class GuardedBash(Bash):
    """Bash with user-configured blacklist checks."""

    def __init__(self, shell_blacklist: list[str], timeout_seconds: int) -> None:
        super().__init__()
        self.shell_blacklist = [item.strip() for item in shell_blacklist if item.strip()]
        self.timeout_seconds = timeout_seconds

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


def _safe_workspace_dir(guardrails: AgentGuardrails | None) -> str:
    scope = (guardrails.filesystem_scope if guardrails else WORKSPACE_DIR) or WORKSPACE_DIR
    expanded = os.path.abspath(os.path.expanduser(scope))
    if expanded == os.path.abspath(os.sep):
        return WORKSPACE_DIR
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

    Returns:
        Toolkit: 包含默认工具的 Toolkit 实例
    """
    local_tools = [
        Read(),
        Write(),
        Edit(),
        Grep(),
        Glob(),
    ]
    if not guardrails or guardrails.network_enabled:
        local_tools.extend(
            [
                GuardedBash(
                    shell_blacklist=guardrails.shell_blacklist if guardrails else [],
                    timeout_seconds=guardrails.timeout_seconds if guardrails else 300,
                ),
                HttpRequest(),
            ]
        )
        for tool in knowledge_tools or []:
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

    mcp_clients = _create_mcp_clients(mcp_servers or [], guardrails)
    return Toolkit(
        tools=[
            *local_tools,
        ],
        mcps=mcp_clients,
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

    # 创建模型
    main_model = _create_model(
        primary_model,
        thinking_enable=thinking_enable,
        model_options=model_options,
    )
    fallback_model = _create_fallback_model(
        fallbacks,
        thinking_enable=thinking_enable,
        model_options=model_options,
    )

    # 创建 LocalWorkspace — Agent 工具执行的隔离环境
    workspace = LocalWorkspace(workdir=_safe_workspace_dir(guardrails))

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
        ),
        state=agent_state,
        offloader=workspace,
        model_config=ModelConfig(
            max_retries=3,
            fallback_model=fallback_model,
        ),
    )

    # 缓存并返回
    _agent_cache[agent_id] = agent
    return agent


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
