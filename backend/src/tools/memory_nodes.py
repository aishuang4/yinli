"""长期节点记忆查询工具。"""

from __future__ import annotations

import json
from typing import Any

from agentscope.message import TextBlock, ToolResultState
from agentscope.permission import PermissionBehavior, PermissionContext, PermissionDecision
from agentscope.tool import ToolBase, ToolChunk

from src.memory.nodes import MemoryNodeService


class MemoryNodeTree(ToolBase):
    """查看长期节点记忆树。"""

    name: str = "memory_node_tree"
    description: str = (
        "查看最近 30 天或指定日期范围内的长期记忆节点树。"
        "只返回日期、Agent、标题、摘要和标签，不返回完整历史详情。"
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "agentId": {"type": "string", "description": "可选 Agent ID 过滤。"},
            "dateFrom": {"type": "string", "description": "可选开始日期，格式 YYYY-MM-DD。"},
            "dateTo": {"type": "string", "description": "可选结束日期，格式 YYYY-MM-DD。"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100, "description": "最大返回节点数。"},
        },
        "required": [],
    }
    is_mcp: bool = False
    is_read_only: bool = True
    is_concurrency_safe: bool = True
    is_external_tool: bool = False
    is_state_injected: bool = False

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="memory_node_tree 只读取长期节点记忆摘要。",
            decision_reason="Read-only memory query",
        )

    async def __call__(
        self,
        agentId: str | None = None,
        dateFrom: str | None = None,
        dateTo: str | None = None,
        limit: int = 30,
        **kwargs: Any,
    ) -> ToolChunk:
        payload = MemoryNodeService().tree(
            agent_id=agentId or None,
            date_from=dateFrom or None,
            date_to=dateTo or None,
            limit=max(1, min(int(limit or 30), 100)),
        )
        return _json_tool_result(payload)


class MemoryNodeQuery(ToolBase):
    """查询长期节点记忆详情。"""

    name: str = "memory_node_query"
    description: str = (
        "按日期范围和关键词查询长期记忆节点详情。"
        "返回用户输入、Agent 最终输出和节点摘要；不会返回工具调用过程或中间推理。"
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "agentId": {"type": "string", "description": "可选 Agent ID 过滤。"},
            "dateFrom": {"type": "string", "description": "可选开始日期，格式 YYYY-MM-DD。"},
            "dateTo": {"type": "string", "description": "可选结束日期，格式 YYYY-MM-DD。"},
            "query": {"type": "string", "description": "查询关键词或问题。"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20, "description": "最大返回节点数。"},
        },
        "required": [],
    }
    is_mcp: bool = False
    is_read_only: bool = True
    is_concurrency_safe: bool = True
    is_external_tool: bool = False
    is_state_injected: bool = False

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="memory_node_query 只读取长期节点记忆详情。",
            decision_reason="Read-only memory query",
        )

    async def __call__(
        self,
        agentId: str | None = None,
        dateFrom: str | None = None,
        dateTo: str | None = None,
        query: str = "",
        limit: int = 10,
        **kwargs: Any,
    ) -> ToolChunk:
        payload = MemoryNodeService().query(
            query_text=query or "",
            agent_id=agentId or None,
            date_from=dateFrom or None,
            date_to=dateTo or None,
            limit=max(1, min(int(limit or 10), 20)),
        )
        return _json_tool_result(payload)


def _json_tool_result(payload: dict[str, Any]) -> ToolChunk:
    return ToolChunk(
        content=[TextBlock(text=json.dumps(payload, ensure_ascii=False, indent=2))],
        state=ToolResultState.SUCCESS,
        is_last=True,
    )
