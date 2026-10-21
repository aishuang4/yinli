"""旧 Markdown 记忆和待确认候选路由。"""

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
from src.storage.database import (
    count_pending_memory_markdown_candidates,
    get_memory_markdown_candidate,
    get_memory_node,
    list_memory_markdown_candidates,
    list_run_events_by_type,
    record_memory_retrieval_feedback,
    set_memory_markdown_candidate_status,
    update_memory_node_status,
)
from src.vector_store.policy import get_vector_store_policy

router = APIRouter()

@router.get("/markdown-candidates")
async def list_markdown_candidates(
    status: str | None = Query(default="pending", pattern="^(pending|accepted|rejected|failed)$"),
    scope: str | None = Query(default=None, pattern="^(user|agent|global_preference)$"),
    project_slug: Annotated[str, Query(alias="projectSlug")] = "default",
    limit: int = Query(default=50, ge=1, le=200),
) -> MarkdownCandidateListResponse:
    """列出 user / global 层待用户确认的 Markdown 记忆候选。"""

    return MarkdownCandidateListResponse(
        records=list_memory_markdown_candidates(status=status, scope=scope, project_slug=project_slug, limit=limit),
        pendingCount=count_pending_memory_markdown_candidates(project_slug=project_slug),
    )


@router.patch("/markdown-candidates/{record_id}")
async def update_markdown_candidate(
    record_id: str,
    data: MarkdownCandidateAction,
    project_slug: Annotated[str, Query(alias="projectSlug")] = "default",
) -> dict[str, Any]:
    """采纳（真正写入 Markdown）或拒绝一条候选。"""

    from src.memory.reflection import apply_markdown_candidate

    record = get_memory_markdown_candidate(record_id)
    if not record or str(record.get("projectSlug") or "default") != project_slug:
        raise HTTPException(status_code=404, detail="Memory candidate not found")
    if record.get("status") != "pending":
        raise HTTPException(status_code=400, detail="Only pending candidates can be resolved")

    if data.action == "reject":
        updated = set_memory_markdown_candidate_status(record_id, status="rejected", resolution=data.reason or "用户拒绝。")
        _record_memory_management_audit(
            session_id=str(record.get("sourceSessionId") or "memory-page"),
            agent_id=str(record.get("agentId") or ""),
            action="memory_candidate_reject",
            status="completed",
            title="Markdown 记忆候选已拒绝",
            content=data.reason or "用户拒绝。",
            payload={"candidateId": record_id, "scope": record.get("scope"), "section": record.get("section")},
        )
        return updated or record

    ok, detail = await apply_markdown_candidate(record)
    if ok:
        updated = set_memory_markdown_candidate_status(record_id, status="accepted", resolution=detail)
        invalidate_memory_prompts()
        _record_memory_management_audit(
            session_id=str(record.get("sourceSessionId") or "memory-page"),
            agent_id=str(record.get("agentId") or ""),
            action="memory_candidate_accept",
            status="completed",
            title="Markdown 记忆候选已采纳",
            content=str(record.get("note") or record.get("replacementText") or record.get("exactText") or ""),
            payload={"candidateId": record_id, "scope": record.get("scope"), "section": record.get("section"), "path": detail},
        )
        return updated or record
    updated = set_memory_markdown_candidate_status(record_id, status="failed", resolution=detail)
    _record_memory_management_audit(
        session_id=str(record.get("sourceSessionId") or "memory-page"),
        agent_id=str(record.get("agentId") or ""),
        action="memory_candidate_accept",
        status="failed",
        title="Markdown 记忆候选采纳失败",
        content=detail,
        payload={"candidateId": record_id, "scope": record.get("scope"), "reason": detail},
    )
    return updated or record


@router.get("/markdown/agent/{agent_id}")
async def read_agent_markdown_memory(agent_id: str) -> dict[str, Any]:
    """读取 Agent Markdown 记忆；不存在时返回默认模板但不创建文件。"""

    doc = MarkdownMemoryStore().read_agent_memory(agent_id)
    return _markdown_doc_response(doc)


@router.put("/markdown/agent/{agent_id}")
async def write_agent_markdown_memory(agent_id: str, data: MarkdownMemoryUpdate) -> dict[str, Any]:
    """保存 Agent Markdown 记忆；首次保存时创建文件。"""

    doc = MarkdownMemoryStore().write_agent_memory(agent_id, data.content)
    invalidate_memory_prompts()
    return _markdown_doc_response(doc)


@router.get("/markdown/user")
async def read_default_user_markdown_memory() -> dict[str, Any]:
    """读取唯一用户 Markdown 记忆；不存在时返回默认模板但不创建文件。"""

    doc = MarkdownMemoryStore().read_user_memory("default")
    return _markdown_doc_response(doc)


@router.put("/markdown/user")
async def write_default_user_markdown_memory(data: MarkdownMemoryUpdate) -> dict[str, Any]:
    """保存唯一用户 Markdown 记忆；首次保存时创建文件。"""

    doc = MarkdownMemoryStore().write_user_memory("default", data.content)
    invalidate_memory_prompts()
    return _markdown_doc_response(doc)


@router.get("/markdown/user/{user_id}")
async def read_user_markdown_memory(user_id: str) -> dict[str, Any]:
    """读取用户 Markdown 记忆；不存在时返回默认模板但不创建文件。"""

    doc = MarkdownMemoryStore().read_user_memory(user_id)
    return _markdown_doc_response(doc)


@router.put("/markdown/user/{user_id}")
async def write_user_markdown_memory(user_id: str, data: MarkdownMemoryUpdate) -> dict[str, Any]:
    """保存用户 Markdown 记忆；首次保存时创建文件。"""

    doc = MarkdownMemoryStore().write_user_memory(user_id, data.content)
    invalidate_memory_prompts()
    return _markdown_doc_response(doc)


@router.get("/markdown/global")
async def read_default_global_markdown_memory() -> dict[str, Any]:
    """读取唯一全局偏好 Markdown 记忆；不存在时返回默认模板但不创建文件。"""

    doc = MarkdownMemoryStore().read_global_memory()
    return _markdown_doc_response(doc)


@router.put("/markdown/global")
async def write_default_global_markdown_memory(data: MarkdownMemoryUpdate) -> dict[str, Any]:
    """保存唯一全局偏好 Markdown 记忆；首次保存时创建文件。"""

    doc = MarkdownMemoryStore().write_global_memory(data.content)
    invalidate_memory_prompts()
    return _markdown_doc_response(doc)


