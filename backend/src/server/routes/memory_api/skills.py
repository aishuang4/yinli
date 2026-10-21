"""Skill 自进化队列路由。"""

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

@router.get("/skill-evolution/records")
async def skill_evolution_records(
    status: str | None = Query(default=None, pattern="^(draft|registered|skipped|failed)$"),
    agent_id: str | None = Query(default=None, alias="agentId"),
    project_slug: Annotated[str, Query(alias="projectSlug")] = "default",
    limit: int = Query(default=50, ge=1, le=200),
) -> SkillEvolutionRecordListResponse:
    """列出 Skill 自进化候选队列，供 Memory 页面查看中间态。"""

    records = list_skill_evolution_records(
        status=status,
        agent_id=agent_id,
        project_slug=project_slug,
        limit=limit,
    )
    return SkillEvolutionRecordListResponse(records=records)


@router.patch("/skill-evolution/records/{record_id}")
async def update_skill_evolution_record(
    record_id: str,
    data: SkillEvolutionRecordAction,
    project_slug: Annotated[str, Query(alias="projectSlug")] = "default",
) -> dict[str, Any]:
    """操作 Skill 自进化候选队列。"""

    record = get_skill_evolution_record(record_id)
    if not record or str(record.get("projectSlug") or "default") != project_slug:
        raise HTTPException(status_code=404, detail="Skill evolution record not found")

    action = data.action
    if action == "register":
        updated, reason = register_skill_evolution_record(record)
        if updated:
            _record_memory_management_audit(
                session_id=str(record.get("sourceSessionId") or "memory-page"),
                agent_id=str(record.get("agentId") or ""),
                action="skill_evolution_register",
                status="completed",
                title="Skill 自进化候选注册",
                content=reason or "已注册为标准 Skill。",
                payload={
                    "recordId": record_id,
                    "registeredSkillId": updated.get("registeredSkillId"),
                    "reason": reason,
                },
            )
            return updated
        failed = update_skill_evolution_record_status(
            record_id,
            status="failed",
            risk_notes=f"手动注册失败：{reason}",
        )
        _record_memory_management_audit(
            session_id=str(record.get("sourceSessionId") or "memory-page"),
            agent_id=str(record.get("agentId") or ""),
            action="skill_evolution_register",
            status="failed",
            title="Skill 自进化候选注册失败",
            content=reason,
            payload={"recordId": record_id, "reason": reason},
        )
        return failed or record

    if action == "restore_draft" and record.get("status") == "registered":
        raise HTTPException(status_code=400, detail="Registered skill evolution record cannot be restored to draft")

    next_status = {
        "skip": "skipped",
        "restore_draft": "draft",
        "mark_failed": "failed",
    }[action]
    risk_notes = data.reason or (
        "用户手动跳过。" if action == "skip" else
        "用户手动标记失败。" if action == "mark_failed" else
        None
    )
    updated = update_skill_evolution_record_status(
        record_id,
        status=next_status,
        risk_notes=risk_notes,
        clear_registered_skill=action == "restore_draft",
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Skill evolution record not found")
    _record_memory_management_audit(
        session_id=str(record.get("sourceSessionId") or "memory-page"),
        agent_id=str(record.get("agentId") or ""),
        action=f"skill_evolution_{action}",
        status="completed",
        title=f"Skill 自进化候选状态更新为 {next_status}",
        content=data.reason,
        payload={
            "recordId": record_id,
            "previousStatus": record.get("status"),
            "nextStatus": next_status,
            "reason": data.reason,
        },
    )
    return updated

