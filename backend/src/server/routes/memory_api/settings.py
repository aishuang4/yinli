"""记忆设置、准入和审计路由。"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query

from src.memory.markdown_store import MarkdownMemoryStore
from src.memory.nodes import MemoryNodeService, MemoryNodeWrite
from src.memory.prompt_cache import invalidate_memory_prompts
from src.memory.settings import get_memory_admission_policy, save_memory_admission_policy
from src.memory.skill_evolution import register_skill_evolution_record
from src.memory.timeline import search_timeline
from src.memory.trace import MEMORY_AUDIT_EVENT_TYPE, record_memory_audit_event
from src.server.routes.memory_api.models import *
from src.server.routes.memory_api.support import _markdown_doc_response, _record_memory_management_audit
from src.storage.database import (
    count_pending_memory_markdown_candidates,
    get_memory_markdown_candidate,
    get_memory_node,
    get_skill_evolution_record,
    list_memory_markdown_candidates,
    list_run_events_by_type,
    list_skill_evolution_records,
    record_memory_retrieval_feedback,
    set_memory_markdown_candidate_status,
    update_memory_node_status,
    update_skill_evolution_record_status,
)
from src.vector_store.policy import get_vector_store_policy

router = APIRouter()

@router.get("/audit/events")
async def memory_audit_events(
    project_slug: Annotated[str, Query(alias="projectSlug")] = "default",
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> MemoryAuditEventListResponse:
    """列出记忆审计事件。"""

    events = list_run_events_by_type(
        MEMORY_AUDIT_EVENT_TYPE,
        project_slug=project_slug,
        include_payload=True,
        limit=limit,
        offset=offset,
    )
    return MemoryAuditEventListResponse(events=events)


@router.post("/retrieval/feedback")
async def record_retrieval_feedback(data: MemoryRetrievalFeedback) -> dict[str, Any]:
    """记录用户对历史记忆召回命中的相关性反馈。"""

    node = get_memory_node(data.node_id)
    if not node or str(node.get("projectSlug") or "default") != data.project_slug:
        raise HTTPException(status_code=404, detail="Memory node not found")
    feedback = record_memory_retrieval_feedback(
        {
            "audit_event_id": data.audit_event_id,
            "node_id": data.node_id,
            "rating": data.rating,
            "query": data.query,
            "agent_id": data.agent_id or str(node.get("agent_id") or ""),
            "session_id": data.session_id or "memory-page",
            "reason": data.reason,
        }
    )
    status = "positive" if data.rating == "relevant" else "negative"
    _record_memory_management_audit(
        session_id=data.session_id or "memory-page",
        agent_id=data.agent_id or str(node.get("agent_id") or ""),
        action="memory_retrieval_feedback",
        status=status,
        title="历史记忆召回反馈",
        content=data.reason,
        payload={
            "auditEventId": data.audit_event_id,
            "nodeId": data.node_id,
            "rating": data.rating,
            "reason": data.reason,
            "query": data.query,
            "source": "memory_page",
            "feedbackId": feedback["id"],
        },
    )
    return {
        "ok": True,
        "feedbackId": feedback["id"],
        "nodeId": data.node_id,
        "rating": data.rating,
    }


@router.get("/settings")
async def memory_settings() -> MemorySettingsResponse:
    """返回 Memory 页面只读设置快照。"""

    policy = get_vector_store_policy()
    admission_policy = get_memory_admission_policy()
    store = MarkdownMemoryStore()
    return MemorySettingsResponse(
        vectorStore={
            "provider": policy.provider,
            "embeddingProvider": policy.embedding_provider,
            "embeddingModel": policy.embedding_model,
            "embeddingDimensions": policy.embedding_dimensions,
            "retrievalLevel": policy.retrieval_level,
            "schemaVersion": policy.schema_version,
        },
        admission=admission_policy.to_api_dict(),
        markdownPaths={
            "root": str(store.root),
            "user": str(store.root / "users" / "user_memory.md"),
            "agentDefault": str(store.root / "agents" / "agent_memory.md"),
            "global": str(store.root / "global" / "global_memory.md"),
        },
        runtime={
            "memoryRuntimeKind": False,
            "usesHooksAndTools": True,
            "chatShowsMemoryProcess": False,
        },
    )


@router.put("/settings/admission")
async def update_memory_admission_settings(data: MemoryAdmissionPolicyUpdate) -> dict[str, Any]:
    """更新 MemoryAdmissionGate 可热更新策略。"""

    payload = data.model_dump(mode="json", by_alias=True, exclude_none=True)
    policy = save_memory_admission_policy(payload)
    _record_memory_management_audit(
        session_id="memory-page",
        agent_id="",
        action="memory_admission_policy_update",
        status="completed",
        title="Memory 准入策略已更新",
        content="用户在 Memory 设置页更新准入阈值和质量策略。",
        payload={"admission": policy.to_api_dict()},
    )
    return policy.to_api_dict()

