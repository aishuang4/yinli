"""历史记忆节点路由。"""
from __future__ import annotations


from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query

from src.memory.markdown_store import MarkdownMemoryStore
from src.memory.nodes import MemoryNodeService, MemoryNodeWrite
from src.memory.prompt_cache import invalidate_memory_prompts
from src.memory.settings import get_memory_admission_policy, save_memory_admission_policy
from src.memory.timeline import search_timeline
from src.memory.trace import MEMORY_AUDIT_EVENT_TYPE, record_memory_audit_event
from src.server.routes.memory_api.models import *
from src.server.routes.memory_api.support import _markdown_doc_response, _record_memory_management_audit
from src.storage.records.memory_markdown_candidates import (
    count_pending_memory_markdown_candidates,
    get_memory_markdown_candidate,
    list_memory_markdown_candidates,
    set_memory_markdown_candidate_status,
)
from src.storage.records.memory_records import (
    get_memory_node,
    record_memory_retrieval_feedback,
    update_memory_node_status,
)
from src.storage.records.trace_records import list_run_events_by_type
from src.vector_store.policy import get_vector_store_policy

router = APIRouter()

@router.post("/nodes")
async def upsert_node(data: MemoryNodeCreate) -> dict[str, Any]:
    """写入长期节点记忆。"""

    item = MemoryNodeWrite(
        id=data.id,
        project_slug=data.project_slug,
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
    project_slug: str = Query(default="default", alias="projectSlug"),
    user_id: str | None = Query(default=None, alias="userId"),
    status: str = Query(default="active", pattern="^(active|disabled|archived|deleted|all)$"),
) -> dict[str, Any]:
    """返回长期节点记忆树。"""

    return MemoryNodeService().tree(
        agent_id=agent_id,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        project_slug=project_slug,
        user_id=user_id,
        status=None if status == "all" else status,
    )


@router.get("/nodes/query")
async def node_query(
    query: str = Query(default=""),
    agent_id: str | None = Query(default=None, alias="agentId"),
    date_from: str | None = Query(default=None, alias="dateFrom"),
    date_to: str | None = Query(default=None, alias="dateTo"),
    limit: int = Query(default=10, ge=1, le=100),
    project_slug: str = Query(default="default", alias="projectSlug"),
    user_id: str | None = Query(default=None, alias="userId"),
    status: str = Query(default="active", pattern="^(active|disabled|archived|deleted|all)$"),
) -> dict[str, Any]:
    """查询长期节点记忆详情。"""

    return MemoryNodeService().query(
        query_text=query,
        agent_id=agent_id,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        project_slug=project_slug,
        user_id=user_id,
        status=None if status == "all" else status,
    )


@router.get("/nodes/{node_id}")
async def get_node(
    node_id: str,
    project_slug: Annotated[str, Query(alias="projectSlug")] = "default",
) -> dict[str, Any]:
    """读取单个长期节点记忆。"""

    node = get_memory_node(node_id)
    if not node or str(node.get("projectSlug") or "default") != project_slug:
        raise HTTPException(status_code=404, detail="Memory node not found")
    return node


@router.patch("/nodes/{node_id}/status")
async def update_node_status(
    node_id: str,
    data: MemoryNodeStatusUpdate,
    project_slug: Annotated[str, Query(alias="projectSlug")] = "default",
) -> dict[str, Any]:
    """软更新历史记忆状态，并尽力同步向量索引。"""

    existing = get_memory_node(node_id)
    if not existing or str(existing.get("projectSlug") or "default") != project_slug:
        raise HTTPException(status_code=404, detail="Memory node not found")
    updated = update_memory_node_status(node_id, status=data.status)
    invalidate_memory_prompts()
    if not updated:
        raise HTTPException(status_code=404, detail="Memory node not found")
    synced = MemoryNodeService().sync_node_to_vector(node_id)
    _record_memory_management_audit(
        session_id=str(existing.get("source_session_id") or "memory-page"),
        agent_id=str(existing.get("agent_id") or ""),
        action="memory_node_status_update",
        status="completed",
        title=f"历史记忆状态更新为 {data.status}",
        content=data.reason,
        payload={
            "nodeId": node_id,
            "previousStatus": existing.get("status"),
            "nextStatus": data.status,
            "reason": data.reason,
            "vectorStatus": synced.get("vector_status"),
            "vectorError": synced.get("vector_error"),
        },
    )
    return synced
