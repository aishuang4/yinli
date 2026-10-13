"""Agent 运行资源的公开组装。

群聊 Worker 用这里拿 MCP、知识库工具和文件范围交集，不再调用 Chat 的底线函数。
"""

from __future__ import annotations

from src.agents.factory import AgentGuardrails, KnowledgeToolRuntimeSpec, McpRuntimeSpec
from src.server.routes.chat.runtime import (
    _build_runtime_guardrails,
    _build_runtime_knowledge_tools,
    _build_runtime_mcp_servers,
    scope_guardrails_to_project,
)

__all__ = [
    "build_runtime_guardrails",
    "build_runtime_knowledge_tools",
    "build_runtime_mcp_servers",
    "scope_guardrails_to_project",
]


def build_runtime_guardrails(agent_config: object | None) -> AgentGuardrails:
    """从 Agent 配置复制一份运行时 guardrails。"""

    return _build_runtime_guardrails(agent_config)


def build_runtime_mcp_servers(
    agent_config: object | None,
    *,
    allowed_server_ids: set[str] | frozenset[str] | None = None,
    claimed_tool_ids: set[str] | frozenset[str] | None = None,
) -> list[McpRuntimeSpec]:
    """按 Project 白名单组装本轮 MCP。"""

    return _build_runtime_mcp_servers(
        agent_config,
        allowed_server_ids=allowed_server_ids,
        claimed_tool_ids=claimed_tool_ids,
    )


def build_runtime_knowledge_tools(
    agent_config: object | None,
    *,
    allowed_tool_ids: set[str] | frozenset[str] | None = None,
) -> list[KnowledgeToolRuntimeSpec]:
    """按 Project 白名单组装本轮知识库工具。"""

    return _build_runtime_knowledge_tools(
        agent_config,
        allowed_tool_ids=allowed_tool_ids,
    )
