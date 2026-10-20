"""习惯、专业区和时间线路由。"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query

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


@router.get("/habits")
async def read_habits_memory(user_id: str = Query(default="default", alias="userId")) -> dict[str, Any]:
    """读取这个人的习惯全文。查询参数保留，值固定为租户内的 default。"""

    del user_id
    return _markdown_doc_response(MarkdownMemoryStore().read_habits("default"))


@router.put("/habits")
async def write_habits_memory(
    data: MarkdownMemoryUpdate,
    user_id: str = Query(default="default", alias="userId"),
) -> dict[str, Any]:
    """覆盖保存习惯全文，并让下一轮注入重新读取。"""

    del user_id
    doc = MarkdownMemoryStore().write_habits("default", data.content)
    invalidate_memory_prompts()
    return _markdown_doc_response(doc)


@router.get("/professional/{agent_id}")
async def read_professional_memory(agent_id: str) -> dict[str, Any]:
    """读取某个智能体的专业区全文。"""

    return _markdown_doc_response(MarkdownMemoryStore().read_professional(agent_id))


@router.put("/professional/{agent_id}")
async def write_professional_memory(agent_id: str, data: MarkdownMemoryUpdate) -> dict[str, Any]:
    """覆盖保存专业区全文。"""

    doc = MarkdownMemoryStore().write_professional(agent_id, data.content)
    invalidate_memory_prompts(agent_id=agent_id)
    return _markdown_doc_response(doc)


@router.get("/timeline")
async def read_memory_timeline(
    workspace_id: str = Query(default="", alias="workspaceId"),
    project_id: str = Query(default="", alias="projectId"),
    agent_id: str = Query(default="", alias="agentId"),
    surface: str = Query(default=""),
    time_from: str = Query(default="", alias="timeFrom"),
    time_to: str = Query(default="", alias="timeTo"),
    speaker: str = Query(default=""),
    query_text: str = Query(default="", alias="queryText"),
    session_id: str = Query(default="", alias="sessionId"),
) -> dict[str, Any]:
    """按标签和时间查看时间线原文，不调用模型。"""

    return search_timeline(
        workspace_id=workspace_id,
        project_id=project_id,
        session_id=session_id,
        agent_id=agent_id,
        surface=surface,
        time_from=time_from,
        time_to=time_to,
        speaker=speaker,
        query_text=query_text,
    )

