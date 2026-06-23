"""
Agent 工厂模块 — 从配置创建 AgentScope 2.0 Agent 实例

职责：
1. 根据 Agent 配置创建 DashScope 模型（主模型 + Fallback 链）
2. 注入默认工具集（Read/Write/Edit/Grep/Glob/Bash/HttpRequest）
3. 配置 LocalWorkspace 作为工具执行环境
4. 设置 BYPASS 权限模式（工具自动执行，无需确认）
5. 管理 Agent 实例缓存（避免重复创建）
"""

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Callable

from pydantic import SecretStr

from agentscope.agent import Agent
from agentscope.agent._config import ContextConfig, ModelConfig, ReActConfig
from agentscope.model import (
    AnthropicChatModel,
    ChatModelBase,
    ChatResponse,
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
from agentscope.message import TextBlock, ToolResultState, UserMsg
from agentscope.tool import Toolkit, Bash, Read, Write, Edit, Grep, Glob, ToolBase, ToolChoice, ToolChunk
from agentscope.state import AgentState
from agentscope.permission import PermissionBehavior, PermissionContext, PermissionDecision
from agentscope.permission._types import PermissionMode
from agentscope.workspace import DockerWorkspace, LocalWorkspace

from src.config.settings import settings
from src.config.runtime_settings import get_effective_model_settings
from src.security.permissions import (
    ApprovalPolicy,
    UnifiedPermissionProfile,
    build_permission_profile,
)
from src.tools.ask_user import AskUser
from src.tools.http_request import HttpRequest
from src.tools.configured_http_tool import ConfiguredHttpTool
from src.tools.knowledge_base_search import KnowledgeBaseSearch
from src.tools.plan_runtime import create_plan_runtime_tools


# ============================================================================
# Workspace 目录 — Agent 工具执行的工作空间
# 默认使用用户主目录，让 Agent 能访问真实项目文件
# ============================================================================
WORKSPACE_DIR = os.path.expanduser("~")

# ============================================================================
# Agent 实例缓存 — 按 agent_id 缓存，避免重复创建
# ============================================================================
_agent_cache: dict[str, Agent] = {}
FallbackEventCallback = Callable[[dict[str, Any]], None]
SubagentEventCallback = Callable[[dict[str, Any]], None]

CONTEXT_SUMMARY_FIELD_LIMIT = 2000
CONTEXT_SUMMARY_NEXT_STEPS_LIMIT = 1200


@dataclass(frozen=True)
class AgentModelOptions:
    """Runtime model parameters selected from the persisted Agent config."""

    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    max_react_iters: int = 40


@dataclass(frozen=True)
class AgentGuardrails:
    """Runtime guardrails used while constructing the Agent."""

    filesystem_scope: str = WORKSPACE_DIR
    shell_blacklist: list[str] = field(default_factory=list)
    network_enabled: bool = True
    human_approval: bool = False
    tool_permission_mode: str = "accept_edits"
    tool_permission_profile: str = "confirm_destructive"
    execution_backend: str = "local"
    docker_image: str = "python:3.11-slim"
    persistent_sandbox: bool = False
    timeout_seconds: int = 300
    permission_profile: UnifiedPermissionProfile = field(default_factory=UnifiedPermissionProfile)

    def __post_init__(self) -> None:
        """兼容旧 guardrails 字段，自动补齐统一权限配置。"""

        profile = self.permission_profile
        if profile == UnifiedPermissionProfile():
            profile = build_permission_profile(self)
        object.__setattr__(self, "permission_profile", profile)


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
    kind: str = "legacy_knowledge"
    description: str = ""
    url: str = ""
    authorization: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    default_payload: dict[str, object] = field(default_factory=dict)
    parameter_instructions: str = ""
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SubagentRuntimeSpec:
    """主 Agent 可调用的子 Agent 运行时配置。"""

    id: str
    name: str
    description: str
    system_prompt: str
    model_name: str
    fallback_names: list[str] = field(default_factory=list)
    model_options: AgentModelOptions = field(default_factory=AgentModelOptions)
    guardrails: AgentGuardrails = field(default_factory=AgentGuardrails)
    mcp_servers: list[McpRuntimeSpec] = field(default_factory=list)
    knowledge_tools: list[KnowledgeToolRuntimeSpec] = field(default_factory=list)
    subagents: list["SubagentRuntimeSpec"] = field(default_factory=list)
    max_depth: int = 2
    memory_enabled: bool = True


@dataclass
class SubagentRuntimeContext:
    """一次主 Agent 运行内共享的子 Agent 调用上下文。"""

    root_agent_id: str
    current_depth: int = 0
    max_depth: int = 2
    call_stack: list[str] = field(default_factory=list)
    max_calls: int = 8
    call_count: int = 0


@dataclass(frozen=True)
class RuntimeToolScope:
    """本轮主 Agent 可见的工具范围。"""

    allow_mcp: bool = True
    allow_knowledge: bool = True
    allow_bash: bool = True
    allow_http: bool = True

    def cache_key(self) -> str:
        return (
            f"mcp-{int(self.allow_mcp)}:"
            f"knowledge-{int(self.allow_knowledge)}:"
            f"bash-{int(self.allow_bash)}:"
            f"http-{int(self.allow_http)}"
        )


def _create_model(
    model_name: str,
    stream: bool = True,
    thinking_enable: bool = False,
    model_options: AgentModelOptions | None = None,
):
    """
    创建当前运行供应商的聊天模型实例

    Args:
        model_name: 模型名称（如 deepseek-chat、gpt-4.1、本地 OpenAI 兼容模型名）
        stream: 是否启用流式输出

    Returns:
        ChatModelBase: 配置好的模型实例
    """
    parameters: dict[str, object] = {
        "thinking_enable": thinking_enable,
    }
    if model_options:
        if model_options.temperature is not None:
            parameters["temperature"] = model_options.temperature
        if model_options.top_p is not None:
            parameters["top_p"] = model_options.top_p
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


def _build_context_config() -> ContextConfig:
    """创建 AgentScope 上下文压缩配置，避免默认摘要字段过短导致运行中断。"""

    summary_schema = {
        "title": "SummarySchema",
        "description": "The compressed memory model, used to generate summary of old memories",
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
                "description": "需要保留的用户偏好、项目约定和上下文。",
            },
        },
        "required": [
            "task_overview",
            "current_state",
            "important_discoveries",
            "next_steps",
            "context_to_preserve",
        ],
    }
    return ContextConfig(
        compression_prompt=(
            "<system-hint>You are compressing the previous conversation so the "
            "agent can continue reliably. Keep every field concise, factual, "
            "and under the schema maxLength. Prefer compact Chinese bullets "
            "when the conversation is Chinese. Do not copy long tool outputs "
            "verbatim.</system-hint>"
        ),
        summary_schema=summary_schema,
        tool_result_limit=4000,
    )


class FallbackChainModel(ChatModelBase):
    """Application-level multi-step fallback model chain."""

    def __init__(
        self,
        models: list[ChatModelBase],
        on_event: FallbackEventCallback | None = None,
    ) -> None:
        if not models:
            raise ValueError("FallbackChainModel requires at least one model")
        self.models = models
        self.on_event = on_event
        super().__init__(
            credential=models[0].credential,
            model=" -> ".join(model.model for model in models),
            parameters=models[0].parameters,
            stream=models[0].stream,
            max_retries=0,
            context_size=models[0].context_size,
        )

    async def _call_api(
        self,
        model_name: str,
        messages: list,
        tools: list[dict] | None = None,
        tool_choice: ToolChoice | None = None,
        **kwargs: Any,
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        """按 AgentScope ChatModelBase 协议执行模型降级链。"""

        last_error: Exception | None = None
        total = len(self.models)
        for index, model in enumerate(self.models):
            self._emit({
                "type": "attempt",
                "model": model.model,
                "index": index,
                "total": total,
            })
            try:
                response = await model(
                    messages=messages,
                    tools=tools,
                    tool_choice=tool_choice,
                    **kwargs,
                )
                self._emit({
                    "type": "success",
                    "model": model.model,
                    "index": index,
                    "total": total,
                })
                return response
            except Exception as exc:
                last_error = exc
                self._emit({
                    "type": "failure",
                    "model": model.model,
                    "index": index,
                    "total": total,
                    "error": str(exc),
                })
        if last_error:
            raise last_error
        raise RuntimeError("Fallback chain failed without an exception")

    def _emit(self, event: dict[str, Any]) -> None:
        if not self.on_event:
            return
        try:
            self.on_event(event)
        except Exception:
            pass


def _create_model_chain(
    model_names: list[str],
    thinking_enable: bool = False,
    model_options: AgentModelOptions | None = None,
    on_fallback_event: FallbackEventCallback | None = None,
) -> ChatModelBase:
    """Create a primary + fallback chain as one AgentScope-compatible model."""
    cleaned_names = [name for name in model_names if name]
    if not cleaned_names:
        raise ValueError("At least one model name is required")
    models = [
        _create_model(
            name,
            stream=True,
            thinking_enable=thinking_enable,
            model_options=model_options,
        )
        for name in cleaned_names
    ]
    if len(models) == 1:
        return models[0]
    return FallbackChainModel(models, on_event=on_fallback_event)


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


def _safe_tool_suffix(name: str) -> str:
    """把 Agent 名称转换为 AgentScope 工具名后缀。"""

    suffix = re.sub(r"[^a-zA-Z0-9_]", "_", name).strip("_") or "Subagent"
    return suffix[:48]


def _trim_tool_text(value: Any, limit: int = 8000) -> str:
    """裁剪工具输出，避免把超长子 Agent 回复塞回主 Agent 上下文。"""

    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n...[truncated {len(text) - limit} chars]"


def _is_read_only_guardrails(guardrails: AgentGuardrails | None) -> bool:
    profile = guardrails.permission_profile if guardrails else UnifiedPermissionProfile()
    return profile.tool_policy == ApprovalPolicy.READ_ONLY


def _restrict_child_guardrails(parent: AgentGuardrails | None, child: AgentGuardrails) -> AgentGuardrails:
    """按主 Agent 安全边界收紧子 Agent 权限。"""

    profile_name = child.tool_permission_profile
    if _is_read_only_guardrails(parent):
        profile_name = "read_only"

    network_enabled = child.network_enabled and bool(parent.network_enabled if parent else True)
    execution_backend = child.execution_backend
    filesystem_scope = child.filesystem_scope
    docker_image = child.docker_image
    persistent_sandbox = child.persistent_sandbox
    if parent and parent.execution_backend == "docker":
        execution_backend = "docker"
        filesystem_scope = parent.filesystem_scope
        docker_image = parent.docker_image
        persistent_sandbox = parent.persistent_sandbox

    restricted = AgentGuardrails(
        filesystem_scope=filesystem_scope,
        shell_blacklist=list({*child.shell_blacklist, *(parent.shell_blacklist if parent else [])}),
        network_enabled=network_enabled,
        human_approval=child.human_approval or bool(parent.human_approval if parent else False),
        tool_permission_mode=child.tool_permission_mode,
        tool_permission_profile=profile_name,
        execution_backend=execution_backend,
        docker_image=docker_image,
        persistent_sandbox=persistent_sandbox,
        timeout_seconds=min(
            max(1, child.timeout_seconds),
            max(1, parent.timeout_seconds if parent else child.timeout_seconds),
        ),
    )
    return AgentGuardrails(
        filesystem_scope=restricted.filesystem_scope,
        shell_blacklist=restricted.shell_blacklist,
        network_enabled=restricted.network_enabled,
        human_approval=restricted.human_approval,
        tool_permission_mode=restricted.tool_permission_mode,
        tool_permission_profile=restricted.tool_permission_profile,
        execution_backend=restricted.execution_backend,
        docker_image=restricted.docker_image,
        persistent_sandbox=restricted.persistent_sandbox,
        timeout_seconds=restricted.timeout_seconds,
        permission_profile=build_permission_profile(restricted),
    )


class CallAgentTool(ToolBase):
    """把一个子 Agent 暴露为主 Agent 可调用工具。"""

    is_mcp: bool = False
    is_concurrency_safe: bool = False
    is_read_only: bool = True
    is_external_tool: bool = False
    is_state_injected: bool = False

    def __init__(
        self,
        *,
        parent_agent_id: str,
        parent_agent_name: str,
        spec: SubagentRuntimeSpec,
        parent_guardrails: AgentGuardrails | None,
        context: SubagentRuntimeContext,
        on_event: SubagentEventCallback | None = None,
        output_limit: int = 8000,
    ) -> None:
        self.parent_agent_id = parent_agent_id
        self.parent_agent_name = parent_agent_name
        self.spec = spec
        self.parent_guardrails = parent_guardrails
        self.context = context
        self.on_event = on_event
        self.output_limit = output_limit
        self.name = f"CallAgent_{_safe_tool_suffix(spec.name)}"[:64]
        self.description = "\n".join(
            part for part in [
                f"Call subagent {spec.name} to complete a delegated task.",
                spec.description,
                "Use this when the task needs this subagent's role, tools, memory, or specialized prompt.",
                "Input must include task; context and expected_output are optional.",
            ] if part
        )
        self.input_schema = {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "要交给子 Agent 完成的具体任务。",
                },
                "context": {
                    "type": "string",
                    "description": "必要上下文，可为空。",
                },
                "expected_output": {
                    "type": "string",
                    "description": "期望输出格式，可为空。",
                },
            },
            "required": ["task"],
            "additionalProperties": False,
        }

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        return PermissionDecision(
            behavior=PermissionBehavior.PASSTHROUGH,
            message=f"Subagent call requires normal permission evaluation: {self.spec.name}",
            decision_reason="Subagent delegation",
        )

    async def check_read_only(self, tool_input: dict[str, Any]) -> bool:
        return True

    async def __call__(self, **kwargs: Any) -> ToolChunk:
        task = str(kwargs.get("task") or "").strip()
        context_text = str(kwargs.get("context") or "").strip()
        expected_output = str(kwargs.get("expected_output") or "").strip()
        if not task:
            return self._chunk(self._blocked("missing_task", "Subagent task is required."), error=True)

        blocked = self._blocked_reason()
        if blocked:
            return self._chunk(blocked, error=True)

        self.context.call_count += 1
        depth = self.context.current_depth + 1
        started = time.perf_counter()
        self._emit("SUBAGENT_CALL_START", {
            "status": "running",
            "task": task,
            "depth": depth,
            "maxDepth": self.context.max_depth,
            "outputPreview": "",
            "durationMs": 0,
        })

        child_guardrails = _restrict_child_guardrails(self.parent_guardrails, self.spec.guardrails)
        child_context = SubagentRuntimeContext(
            root_agent_id=self.context.root_agent_id,
            current_depth=depth,
            max_depth=self.context.max_depth,
            call_stack=[*self.context.call_stack, self.spec.id],
            max_calls=self.context.max_calls,
            call_count=self.context.call_count,
        )
        child_subagents = self.spec.subagents if depth < self.context.max_depth else []
        child_runtime_id = (
            f"subagent:{self.context.root_agent_id}:{self.spec.id}:"
            f"depth-{depth}:calls-{self.context.call_count}:stack-{','.join(child_context.call_stack)}"
        )
        prompt = "\n".join(
            part for part in [
                f"任务：{task}",
                f"上下文：{context_text}" if context_text else "",
                f"期望输出：{expected_output}" if expected_output else "",
                "请只完成被委派的任务，并返回可被主 Agent 直接使用的结果。",
            ] if part
        )

        try:
            child_agent = get_or_create_agent(
                agent_id=child_runtime_id,
                name=self.spec.name,
                system_prompt=self.spec.system_prompt,
                model_name=self.spec.model_name,
                fallback_names=self.spec.fallback_names,
                thinking_enable=False,
                permission_mode=child_guardrails.permission_profile.to_agentscope_permission_mode(),
                model_options=self.spec.model_options,
                guardrails=child_guardrails,
                mcp_servers=self.spec.mcp_servers,
                knowledge_tools=self.spec.knowledge_tools,
                subagents=child_subagents,
                subagent_context=child_context,
                parent_agent_id=self.spec.id,
                parent_agent_name=self.spec.name,
                on_subagent_event=self.on_event,
            )
            output = ""
            async for event in child_agent.reply_stream(UserMsg(name="user", content=prompt)):
                event_data = event.model_dump(mode="json")
                event_type = event_data.get("type")
                if event_type == "TEXT_BLOCK_DELTA":
                    output += str(event_data.get("delta") or "")
            duration_ms = int((time.perf_counter() - started) * 1000)
            self.context.call_count = child_context.call_count
            result = {
                "agentId": self.spec.id,
                "agentName": self.spec.name,
                "status": "completed",
                "output": _trim_tool_text(output, self.output_limit),
                "durationMs": duration_ms,
                "error": None,
            }
            self._emit("SUBAGENT_CALL_END", {
                "status": "completed",
                "task": task,
                "depth": depth,
                "maxDepth": self.context.max_depth,
                "durationMs": duration_ms,
                "outputPreview": _trim_tool_text(output, 1200),
            })
            return self._chunk(result)
        except Exception as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            result = {
                "agentId": self.spec.id,
                "agentName": self.spec.name,
                "status": "failed",
                "output": "",
                "durationMs": duration_ms,
                "error": str(exc),
            }
            self._emit("SUBAGENT_CALL_FAILED", {
                "status": "failed",
                "task": task,
                "depth": depth,
                "maxDepth": self.context.max_depth,
                "durationMs": duration_ms,
                "outputPreview": "",
                "error": str(exc),
            })
            return self._chunk(result, error=True)

    def _blocked_reason(self) -> dict[str, Any] | None:
        if self.context.call_count >= self.context.max_calls:
            return self._blocked("max_calls", f"Subagent call limit reached: {self.context.max_calls}.")
        if self.context.current_depth >= self.context.max_depth:
            return self._blocked("max_depth", f"Subagent max depth reached: {self.context.max_depth}.")
        if self.spec.id in self.context.call_stack:
            return self._blocked("cycle_detected", f"Subagent cycle detected: {self.spec.name}.")
        return None

    def _blocked(self, reason: str, message: str) -> dict[str, Any]:
        payload = {
            "agentId": self.spec.id,
            "agentName": self.spec.name,
            "status": "blocked",
            "output": "",
            "durationMs": 0,
            "error": message,
            "reason": reason,
        }
        self._emit("SUBAGENT_CALL_FAILED", {
            "status": "blocked",
            "task": "",
            "depth": self.context.current_depth + 1,
            "maxDepth": self.context.max_depth,
            "durationMs": 0,
            "outputPreview": "",
            "error": message,
            "reason": reason,
        })
        return payload

    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if not self.on_event:
            return
        try:
            self.on_event({
                "eventType": event_type,
                "parentAgentId": self.parent_agent_id,
                "parentAgentName": self.parent_agent_name,
                "subagentId": self.spec.id,
                "subagentName": self.spec.name,
                **payload,
            })
        except Exception:
            pass

    def _chunk(self, result: dict[str, Any], error: bool = False) -> ToolChunk:
        return ToolChunk(
            content=[TextBlock(text=json.dumps(result, ensure_ascii=False, indent=2))],
            state=ToolResultState.ERROR if error else ToolResultState.SUCCESS,
            is_last=True,
        )


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
    subagents: list[SubagentRuntimeSpec] | None = None,
    subagent_context: SubagentRuntimeContext | None = None,
    parent_agent_id: str = "default",
    parent_agent_name: str = "Assistant",
    on_subagent_event: SubagentEventCallback | None = None,
    plan_id: str | None = None,
    tool_scope: RuntimeToolScope | None = None,
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
    permission_profile = guardrails.permission_profile if guardrails else UnifiedPermissionProfile()
    read_only = permission_profile.tool_policy == ApprovalPolicy.READ_ONLY
    scope = tool_scope or RuntimeToolScope()

    local_tools = [
        Read(),
        Grep(),
        Glob(),
        AskUser(),
        *create_plan_runtime_tools(plan_id),
    ]
    if not read_only:
        local_tools.extend([Write(), Edit()])

    if permission_profile.can_use_network:
        network_tools = []
        if scope.allow_bash:
            network_tools.append(
                GuardedBash(
                    shell_blacklist=guardrails.shell_blacklist if guardrails else [],
                    timeout_seconds=guardrails.timeout_seconds if guardrails else 300,
                )
            )
        if scope.allow_http:
            network_tools.append(HttpRequest())
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

    if subagents and subagent_context and not read_only:
        for spec in subagents:
            local_tools.append(
                CallAgentTool(
                    parent_agent_id=parent_agent_id,
                    parent_agent_name=parent_agent_name,
                    spec=spec,
                    parent_guardrails=guardrails,
                    context=subagent_context,
                    on_event=on_subagent_event,
                )
            )

    mcp_clients = [] if read_only or not scope.allow_mcp else _create_mcp_clients(mcp_servers or [], guardrails)
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
    subagents: list[SubagentRuntimeSpec] | None = None,
    subagent_context: SubagentRuntimeContext | None = None,
    parent_agent_id: str | None = None,
    parent_agent_name: str | None = None,
    plan_id: str | None = None,
    tool_scope: RuntimeToolScope | None = None,
    on_fallback_event: FallbackEventCallback | None = None,
    on_subagent_event: SubagentEventCallback | None = None,
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
    main_model = _create_model_chain(
        [primary_model, *fallbacks],
        thinking_enable=thinking_enable,
        model_options=model_options,
        on_fallback_event=on_fallback_event,
    )

    # 创建 Workspace — local 默认，docker 用 AgentScope 内置容器沙盒
    if guardrails and guardrails.execution_backend == "docker":
        workspace = DockerWorkspace(
            workspace_id=f"agent-{agent_id}".replace(":", "-")[:120],
            base_image=guardrails.docker_image or "python:3.11-slim",
            workdir=(
                os.path.join(_safe_workspace_dir(guardrails), ".agent-workstation-sandbox", agent_id.replace(":", "-"))
                if guardrails.persistent_sandbox
                else None
            ),
        )
    else:
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
            subagents=subagents,
            subagent_context=subagent_context,
            parent_agent_id=parent_agent_id or agent_id,
            parent_agent_name=parent_agent_name or name,
            on_subagent_event=on_subagent_event,
            plan_id=plan_id,
            tool_scope=tool_scope,
        ),
        state=agent_state,
        offloader=workspace,
        model_config=ModelConfig(
            max_retries=3,
            fallback_model=None,
        ),
        context_config=_build_context_config(),
        react_config=ReActConfig(
            max_iters=max(1, min(int(model_options.max_react_iters if model_options else 40), 120)),
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
