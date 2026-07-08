"""长期节点记忆查询工具。"""

from __future__ import annotations

import json
from typing import Any

from agentscope.message import TextBlock, ToolResultState
from agentscope.permission import PermissionBehavior, PermissionContext, PermissionDecision
from agentscope.tool import ToolBase, ToolChunk

from src.memory.nodes import MemoryNodeService
from src.memory.trace import record_memory_audit_event


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
    audit_session_id: str = ""
    audit_agent_id: str = ""
    audit_agent_name: str = ""

    def __init__(
        self,
        audit_session_id: str = "",
        audit_agent_id: str = "",
        audit_agent_name: str = "",
    ) -> None:
        super().__init__()
        self.audit_session_id = audit_session_id
        self.audit_agent_id = audit_agent_id
        self.audit_agent_name = audit_agent_name

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
        _record_retrieval_audit(
            session_id=self.audit_session_id,
            agent_id=self.audit_agent_id or agentId or "",
            agent_name=self.audit_agent_name,
            action="memory_node_tree",
            status="completed",
            title="历史记忆树查询",
            query_text="",
            filters={
                "agentId": agentId or "",
                "dateFrom": dateFrom or "",
                "dateTo": dateTo or "",
                "limit": max(1, min(int(limit or 30), 100)),
            },
            result_ids=[str(item.get("nodeId") or "") for item in payload.get("nodes", [])],
            retrieval_meta={
                "returnedCount": len(payload.get("nodes", []) or []),
                "empty": len(payload.get("nodes", []) or []) == 0,
                "retrievalSources": ["sqlite_tree"],
            },
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
    audit_session_id: str = ""
    audit_agent_id: str = ""
    audit_agent_name: str = ""

    def __init__(
        self,
        audit_session_id: str = "",
        audit_agent_id: str = "",
        audit_agent_name: str = "",
    ) -> None:
        super().__init__()
        self.audit_session_id = audit_session_id
        self.audit_agent_id = audit_agent_id
        self.audit_agent_name = audit_agent_name

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
        _record_retrieval_audit(
            session_id=self.audit_session_id,
            agent_id=self.audit_agent_id or agentId or "",
            agent_name=self.audit_agent_name,
            action="memory_node_query",
            status="completed",
            title="历史记忆详情查询",
            query_text=query or "",
            filters={
                "agentId": agentId or "",
                "dateFrom": dateFrom or "",
                "dateTo": dateTo or "",
                "limit": max(1, min(int(limit or 10), 20)),
            },
            result_ids=[str(item.get("nodeId") or "") for item in payload.get("results", [])],
            retrieval_meta=payload.get("meta") if isinstance(payload.get("meta"), dict) else {},
        )
        return _json_tool_result(payload)


def _json_tool_result(payload: dict[str, Any]) -> ToolChunk:
    return ToolChunk(
        content=[TextBlock(text=json.dumps(payload, ensure_ascii=False, indent=2))],
        state=ToolResultState.SUCCESS,
        is_last=True,
    )


def _record_retrieval_audit(
    *,
    session_id: str,
    agent_id: str,
    agent_name: str,
    action: str,
    status: str,
    title: str,
    query_text: str,
    filters: dict[str, Any],
    result_ids: list[str],
    retrieval_meta: dict[str, Any] | None = None,
) -> None:
    """记录 Agent 主动召回历史记忆的审计事件。"""

    clean_result_ids = [item for item in result_ids if item]
    record_memory_audit_event(
        session_id=session_id or "memory-tool",
        persist=True,
        agent_id=agent_id,
        agent_name=agent_name or agent_id or "Memory Tool",
        action=action,
        status=status,
        title=title,
        content=query_text[:500],
        payload={
            "query": query_text,
            "filters": filters,
            "resultCount": len(clean_result_ids),
            "resultNodeIds": clean_result_ids[:20],
            "retrievalSource": "agent_tool",
            "retrievalQuality": retrieval_meta or {},
            "sourceRunId": _current_run_id(),
        },
    )


def _current_run_id() -> str:
    """读取当前 Chat 后台 runId，普通测试或非 Chat 场景下为空。"""

    try:
        from src.server.routes.chat.background_runs import current_chat_background_run_id

        return current_chat_background_run_id()
    except Exception:
        return ""
