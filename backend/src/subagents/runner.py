"""Subagent 前台阻塞运行器。"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from agentscope.message import UserMsg

from src.agents.factory import get_or_create_agent
from src.agents.runtime_types import (
    AgentGuardrails,
    AgentModelOptions,
    KnowledgeToolRuntimeSpec,
    McpRuntimeSpec,
    RuntimeToolScope,
)
from src.subagents.models import SubagentDefinition
from src.subagents.permission_policy import SubagentPermissionResolution, resolve_subagent_permission
from src.subagents.runtime_context import SubagentRuntimeContext, build_subagent_runtime_context
from src.runtime_context import RuntimeContext as PlatformRuntimeContext
from src.project_files import project_session_directory
from src.subagents.tool_policy import SubagentToolPolicy, build_subagent_tool_policy
from src.tools.configured_http_tool import safe_http_tool_name

HITL_EVENT_TYPES = {"REQUIRE_USER_CONFIRM", "REQUIRE_EXTERNAL_EXECUTION"}


@dataclass(frozen=True)
class SubagentRunResult:
    """Subagent 执行结果。"""

    text: str
    duration_ms: int


@dataclass(frozen=True)
class SubagentPreparedRun:
    """Subagent 运行前解析出的上下文。"""

    runtime_context: SubagentRuntimeContext
    permission: SubagentPermissionResolution
    tool_policy: SubagentToolPolicy
    audit_payload: dict[str, Any]


class SubagentRunError(RuntimeError):
    """Subagent 执行失败。"""

    def __init__(self, message: str, *, partial_result: str = "", duration_ms: int = 0) -> None:
        super().__init__(message)
        self.partial_result = partial_result
        self.duration_ms = duration_ms


class SubagentHitlRequired(SubagentRunError):
    """Subagent 暂停在 AgentScope HITL / 外部执行确认点。"""

    def __init__(
        self,
        message: str,
        *,
        agent: object | None = None,
        interaction_type: str,
        reply_id: str,
        tool_calls: list[dict],
        partial_result: str = "",
        duration_ms: int = 0,
    ) -> None:
        super().__init__(message, partial_result=partial_result, duration_ms=duration_ms)
        self.agent = agent
        self.interaction_type = interaction_type
        self.reply_id = reply_id
        self.tool_calls = tool_calls


class SubagentRunner:
    """创建 fresh-context 子 Agent 并同步等待其完成。"""

    def __init__(
        self,
        *,
        session_id: str,
        run_id: str,
        parent_scope: str,
        parent_agent_id: str,
        parent_agent_name: str,
        model_name: str,
        fallback_names: list[str],
        model_options: AgentModelOptions,
        guardrails: AgentGuardrails,
        mcp_servers: list[McpRuntimeSpec],
        knowledge_tools: list[KnowledgeToolRuntimeSpec],
        parent_tool_scope: RuntimeToolScope,
        persist: bool,
        platform_context: PlatformRuntimeContext | None = None,
    ) -> None:
        self.session_id = session_id
        self.run_id = run_id
        self.parent_scope = parent_scope
        self.parent_agent_id = parent_agent_id
        self.parent_agent_name = parent_agent_name
        self.model_name = model_name
        self.fallback_names = fallback_names
        self.model_options = model_options
        self.guardrails = guardrails
        self.mcp_servers = mcp_servers
        self.knowledge_tools = knowledge_tools
        self.parent_tool_scope = parent_tool_scope
        self.persist = persist
        self.platform_context = platform_context

    def prepare(self, definition: SubagentDefinition) -> SubagentPreparedRun:
        """解析 Subagent 运行前上下文，供调用方先写审计事件。"""

        runtime_context = build_subagent_runtime_context(definition, self.platform_context)
        permission = resolve_subagent_permission(definition, self.guardrails)
        tool_policy = build_subagent_tool_policy(
            definition,
            parent_tool_scope=self.parent_tool_scope,
            parent_guardrails=self.guardrails,
            child_guardrails=permission.guardrails,
            mcp_servers=self.mcp_servers,
            knowledge_tools=self.knowledge_tools,
        )
        return SubagentPreparedRun(
            runtime_context=runtime_context,
            permission=permission,
            tool_policy=tool_policy,
            audit_payload={
                **runtime_context.audit_payload(definition),
                "permission": permission.audit_payload(),
                "effectiveTools": sorted(tool_policy.effective_tool_names),
                "deniedTools": sorted(tool_policy.denied_tool_names),
            },
        )

    async def run(
        self,
        *,
        invocation_id: str,
        definition: SubagentDefinition,
        task: str,
        expected_output: str = "",
        context: str = "",
        constraints: list[str] | None = None,
        runtime_context: SubagentRuntimeContext | None = None,
        permission: SubagentPermissionResolution | None = None,
        tool_policy: SubagentToolPolicy | None = None,
        runtime_event_sink: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> SubagentRunResult:
        """运行一个 Subagent invocation。"""

        started = time.monotonic()
        runtime_context = runtime_context or build_subagent_runtime_context(definition, self.platform_context)
        permission = permission or resolve_subagent_permission(definition, self.guardrails)
        policy = tool_policy or build_subagent_tool_policy(
            definition,
            parent_tool_scope=self.parent_tool_scope,
            parent_guardrails=self.guardrails,
            child_guardrails=permission.guardrails,
            mcp_servers=self.mcp_servers,
            knowledge_tools=self.knowledge_tools,
        )
        child_agent_id = f"subagent:{definition.name}:{invocation_id}"
        child_agent_name = f"Subagent {definition.name}"
        workspace_dir = (
            str(project_session_directory(Path(permission.guardrails.filesystem_scope), self.session_id))
            if self.platform_context is not None
            else None
        )
        agent = get_or_create_agent(
            agent_id=child_agent_id,
            name=child_agent_name,
            system_prompt=_build_child_system_prompt(definition, runtime_context),
            model_name=None if definition.model == "inherit" else definition.model,
            fallback_names=self.fallback_names,
            thinking_enable=False,
            permission_mode=permission.agentscope_mode,
            model_options=AgentModelOptions(
                temperature=self.model_options.temperature,
                top_p=self.model_options.top_p,
                max_tokens=self.model_options.max_tokens,
                max_react_iters=definition.max_turns,
            ),
            guardrails=permission.guardrails,
            mcp_servers=_filter_mcp_servers(self.mcp_servers, policy.effective_tool_names),
            knowledge_tools=_filter_knowledge_tools(self.knowledge_tools, policy.effective_tool_names),
            parent_agent_id=f"subagent:{definition.name}",
            parent_agent_name=child_agent_name,
            session_id=self.session_id,
            tool_scope=_child_tool_scope(self.parent_tool_scope, policy.effective_tool_names),
            tool_name_allowlist=policy.allowed_tool_names,
            tool_name_denylist=policy.denied_tool_names,
            skill_paths=runtime_context.skill_paths,
            workspace_dir=workspace_dir,
        )

        final_started = False
        final_text = ""
        reasoning_text = ""
        try:
            async for event in agent.reply_stream(
                UserMsg(
                    name="user",
                    content=_build_child_task_message(
                        task=task,
                        expected_output=expected_output,
                        context=context,
                        constraints=constraints or [],
                    ),
                )
            ):
                event_data = event.model_dump(mode="json")
                event_type = event_data.pop("type", "UNKNOWN")
                _record_child_agent_event(
                    session_id=self.session_id,
                    event_type=event_type,
                    event_data=event_data,
                    persist=self.persist,
                    agent_id=f"subagent:{definition.name}",
                    agent_name=child_agent_name,
                    guardrails=self.guardrails,
                    child_guardrails=permission.guardrails,
                )
                if runtime_event_sink is not None:
                    runtime_event_sink(event_type, event_data)
                if event_type in HITL_EVENT_TYPES:
                    raise SubagentHitlRequired(
                        "Subagent 已暂停在人工确认点，等待后续 HITL resume 主路径接入。",
                        agent=agent,
                        interaction_type=event_type,
                        reply_id=str(event_data.get("reply_id") or ""),
                        tool_calls=list(event_data.get("tool_calls") or []),
                        partial_result=final_text or reasoning_text,
                        duration_ms=_duration_ms(started),
                    )
                if event_type == "EXCEED_MAX_ITERS":
                    raise SubagentRunError(
                        "Subagent 超过最大 ReAct 轮次。",
                        partial_result=final_text or reasoning_text,
                        duration_ms=_duration_ms(started),
                    )
                if _is_final_answer_marker(event_type, event_data):
                    final_started = True
                    continue
                if event_type == "TEXT_BLOCK_DELTA":
                    delta = str(event_data.get("delta") or "")
                    if final_started:
                        final_text += delta
                        if runtime_event_sink is not None:
                            runtime_event_sink(event_type, {
                                **event_data,
                                "subagentFinalAnswerDelta": True,
                            })
                    else:
                        reasoning_text += delta
            if not final_text.strip():
                raise SubagentRunError(
                    "Subagent 未调用 FinalAnswerStart 或未输出最终摘要。",
                    partial_result=reasoning_text,
                    duration_ms=_duration_ms(started),
                )
            return SubagentRunResult(text=final_text.strip(), duration_ms=_duration_ms(started))
        except SubagentRunError:
            raise
        except Exception as exc:
            raise SubagentRunError(
                f"Subagent 执行异常: {exc}",
                partial_result=final_text or reasoning_text,
                duration_ms=_duration_ms(started),
            ) from exc


def _build_child_system_prompt(
    definition: SubagentDefinition,
    runtime_context: SubagentRuntimeContext | None = None,
) -> str:
    runtime_context = runtime_context or build_subagent_runtime_context(definition)
    return "\n\n".join(
        part
        for part in [
            definition.prompt,
            runtime_context.memory_prompt,
            (
                "你是 Agent Workstation 的 Subagent。你在独立上下文中完成委托任务，"
                "不要引用你看不到的主会话历史。完成后必须调用 FinalAnswerStart，"
                "并只输出可被主 Agent 消化的简明结果摘要。"
            ),
        ]
        if part.strip()
    )


def _build_child_task_message(
    *,
    task: str,
    expected_output: str,
    context: str,
    constraints: list[str],
) -> str:
    sections = [f"委托任务:\n{task.strip()}"]
    if expected_output.strip():
        sections.append(f"期望输出:\n{expected_output.strip()}")
    if context.strip():
        sections.append(f"补充上下文:\n{context.strip()}")
    if constraints:
        sections.append("约束:\n" + "\n".join(f"- {item}" for item in constraints if item.strip()))
    return "\n\n".join(sections)


def _is_final_answer_marker(event_type: str, event_data: dict[str, Any]) -> bool:
    if event_type != "TOOL_CALL_START":
        return False
    return str(event_data.get("tool_call_name") or "") == "FinalAnswerStart"


def _duration_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


def _record_child_agent_event(
    *,
    session_id: str,
    event_type: str,
    event_data: dict[str, Any],
    persist: bool,
    agent_id: str,
    agent_name: str,
    guardrails: AgentGuardrails,
    child_guardrails: AgentGuardrails | None = None,
) -> None:
    """延迟导入 Chat trace helper，避免 Subagent 包反向触发 Chat 路由初始化。"""

    from src.server.routes.chat.trace_recorder import _record_agent_scope_event, _record_permission_decision

    _record_agent_scope_event(
        session_id=session_id,
        event_type=event_type,
        event_data=event_data,
        persist=persist,
        scope="subagent",
        agent_id=agent_id,
        agent_name=agent_name,
    )
    _record_permission_decision(
        session_id=session_id,
        event_type=event_type,
        event_data=event_data,
        persist=persist,
        scope="subagent",
        agent_id=agent_id,
        agent_name=agent_name,
        guardrails=child_guardrails or guardrails,
    )


def _child_tool_scope(parent_scope: RuntimeToolScope, effective_tools: set[str]) -> RuntimeToolScope:
    """根据裁剪后的工具名关闭非本地工具入口。"""

    return RuntimeToolScope(
        allow_mcp=parent_scope.allow_mcp and any(tool.startswith("mcp__") for tool in effective_tools),
        allow_knowledge=parent_scope.allow_knowledge and any(
            tool == "KnowledgeBaseSearch"
            or tool.startswith("KnowledgeBaseSearch_")
            or tool.startswith("HttpTool_")
            for tool in effective_tools
        ),
        allow_bash=parent_scope.allow_bash and "Bash" in effective_tools,
        allow_http=parent_scope.allow_http and "HttpRequest" in effective_tools,
        allow_web=parent_scope.allow_web and "WebFetch" in effective_tools,
    )


def _filter_mcp_servers(
    servers: list[McpRuntimeSpec],
    effective_tools: set[str],
) -> list[McpRuntimeSpec]:
    """按有效 MCP 工具名裁剪传入子 Agent 的 MCP server。"""

    filtered: list[McpRuntimeSpec] = []
    for server in servers:
        server_name = str(server.name or "").strip()
        if not server_name:
            continue
        wildcard = f"mcp__{server_name}__*" in effective_tools or f"mcp__{server_name}" in effective_tools
        if wildcard:
            filtered.append(server)
            continue
        if not server.enable_tools:
            enabled = _effective_mcp_tool_names_for_server(server_name, effective_tools)
            if enabled:
                filtered.append(McpRuntimeSpec(
                    name=server.name,
                    transport=server.transport,
                    url=server.url,
                    command=server.command,
                    headers=dict(server.headers),
                    enable_tools=enabled,
                ))
            continue
        enabled = [
            tool_name
            for tool_name in server.enable_tools
            if f"mcp__{server_name}__{tool_name}" in effective_tools
        ]
        if enabled:
            filtered.append(McpRuntimeSpec(
                name=server.name,
                transport=server.transport,
                url=server.url,
                command=server.command,
                headers=dict(server.headers),
                enable_tools=enabled,
            ))
    return filtered


def _effective_mcp_tool_names_for_server(server_name: str, effective_tools: set[str]) -> list[str]:
    prefix = f"mcp__{server_name}__"
    return sorted(
        tool.removeprefix(prefix)
        for tool in effective_tools
        if tool.startswith(prefix) and tool != f"{prefix}*"
    )


def _filter_knowledge_tools(
    tools: list[KnowledgeToolRuntimeSpec],
    effective_tools: set[str],
) -> list[KnowledgeToolRuntimeSpec]:
    """按有效工具名裁剪传入子 Agent 的知识库工具。"""

    return [
        tool
        for tool in tools
        if _knowledge_tool_name(tool) in effective_tools
    ]


def _knowledge_tool_name(tool: KnowledgeToolRuntimeSpec) -> str:
    if tool.kind == "http":
        return safe_http_tool_name(tool.name or "Custom")
    import re

    safe_name = re.sub(r"[^a-zA-Z0-9_]", "_", tool.name or "").strip("_") or "default"
    return (safe_name if safe_name == "KnowledgeBaseSearch" else f"KnowledgeBaseSearch_{safe_name}")[:64]
