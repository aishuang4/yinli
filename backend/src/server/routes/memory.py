"""Memory API 路由。

首版只暴露长期节点记忆，供后续 Tools、Hook 和前端页面复用。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from src.memory.markdown_store import MarkdownMemoryStore
from src.memory.nodes import MemoryNodeService, MemoryNodeWrite
from src.storage.database import get_memory_node

router = APIRouter()


class MemoryNodeCreate(BaseModel):
    """创建或更新长期节点记忆。"""

    agent_id: str = Field(..., alias="agentId", min_length=1)
    node_date: str = Field(..., alias="nodeDate", min_length=10, max_length=10)
    title: str = Field(..., min_length=1, max_length=200)
    summary: str = Field(default="", max_length=4000)
    user_inputs: list[str] = Field(default_factory=list, alias="userInputs")
    agent_final_outputs: list[str] = Field(default_factory=list, alias="agentFinalOutputs")
    conversation_digest: str = Field(default="", alias="conversationDigest", max_length=8000)
    tags: list[str] = Field(default_factory=list)
    workspace_id: str = Field(default="default", alias="workspaceId")
    project_id: str = Field(default="", alias="projectId")
    user_id: str = Field(default="default", alias="userId")
    date_range_start: str | None = Field(default=None, alias="dateRangeStart")
    date_range_end: str | None = Field(default=None, alias="dateRangeEnd")
    source: str = "chat_run"
    source_session_id: str = Field(default="", alias="sourceSessionId")
    source_run_id: str = Field(default="", alias="sourceRunId")
    source_message_id: str = Field(default="", alias="sourceMessageId")
    confidence: float = Field(default=1, ge=0, le=1)
    status: str = Field(default="active", pattern="^(active|disabled|archived|deleted)$")
    pinned: bool = False
    id: str | None = None

    model_config = {"populate_by_name": True}


class MarkdownMemoryUpdate(BaseModel):
    """保存 Markdown 记忆。"""

    content: str = Field(..., max_length=200_000)


@router.post("/nodes")
async def upsert_node(data: MemoryNodeCreate) -> dict[str, Any]:
    """写入长期节点记忆。"""

    item = MemoryNodeWrite(
        id=data.id,
        workspace_id=data.workspace_id,
        project_id=data.project_id,
        agent_id=data.agent_id,
        user_id=data.user_id,
        node_date=data.node_date,
        date_range_start=data.date_range_start,
        date_range_end=data.date_range_end,
        title=data.title,
        summary=data.summary,
        conversation_digest=data.conversation_digest,
        user_inputs=data.user_inputs,
        agent_final_outputs=data.agent_final_outputs,
        tags=data.tags,
        source=data.source,
        source_session_id=data.source_session_id,
        source_run_id=data.source_run_id,
        source_message_id=data.source_message_id,
        confidence=data.confidence,
        status=data.status,
        pinned=data.pinned,
    )
    return MemoryNodeService().upsert_node(item)


@router.get("/nodes/tree")
async def node_tree(
    agent_id: str | None = Query(default=None, alias="agentId"),
    date_from: str | None = Query(default=None, alias="dateFrom"),
    date_to: str | None = Query(default=None, alias="dateTo"),
    limit: int = Query(default=30, ge=1, le=500),
    workspace_id: str = Query(default="default", alias="workspaceId"),
    project_id: str = Query(default="", alias="projectId"),
    user_id: str | None = Query(default=None, alias="userId"),
) -> dict[str, Any]:
    """返回长期节点记忆树。"""

    return MemoryNodeService().tree(
        agent_id=agent_id,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        workspace_id=workspace_id,
        project_id=project_id,
        user_id=user_id,
    )


@router.get("/nodes/query")
async def node_query(
    query: str = Query(default=""),
    agent_id: str | None = Query(default=None, alias="agentId"),
    date_from: str | None = Query(default=None, alias="dateFrom"),
    date_to: str | None = Query(default=None, alias="dateTo"),
    limit: int = Query(default=10, ge=1, le=100),
    workspace_id: str = Query(default="default", alias="workspaceId"),
    project_id: str = Query(default="", alias="projectId"),
    user_id: str | None = Query(default=None, alias="userId"),
) -> dict[str, Any]:
    """查询长期节点记忆详情。"""

    return MemoryNodeService().query(
        query_text=query,
        agent_id=agent_id,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        workspace_id=workspace_id,
        project_id=project_id,
        user_id=user_id,
    )


@router.get("/nodes/{node_id}")
async def get_node(node_id: str) -> dict[str, Any]:
    """读取单个长期节点记忆。"""

    node = get_memory_node(node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Memory node not found")
    return node


@router.get("/markdown/agent/{agent_id}")
async def read_agent_markdown_memory(agent_id: str) -> dict[str, Any]:
    """读取 Agent Markdown 记忆；不存在时返回默认模板但不创建文件。"""

    doc = MarkdownMemoryStore().read_agent_memory(agent_id)
    return {
        "scope": doc.scope,
        "ownerId": doc.owner_id,
        "path": doc.path,
        "content": doc.content,
        "exists": doc.exists,
    }


@router.put("/markdown/agent/{agent_id}")
async def write_agent_markdown_memory(agent_id: str, data: MarkdownMemoryUpdate) -> dict[str, Any]:
    """保存 Agent Markdown 记忆；首次保存时创建文件。"""

    doc = MarkdownMemoryStore().write_agent_memory(agent_id, data.content)
    return {
        "scope": doc.scope,
        "ownerId": doc.owner_id,
        "path": doc.path,
        "content": doc.content,
        "exists": doc.exists,
    }
