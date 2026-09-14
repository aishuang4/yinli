"""Memory API 路由。

暴露长期节点记忆和 Markdown 长期记忆，供后续 Tools、Hook 和前端页面复用。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from src.memory.markdown_store import MarkdownMemoryStore
from src.memory.nodes import MemoryNodeService, MemoryNodeWrite
from src.memory.settings import get_memory_admission_policy, save_memory_admission_policy
from src.memory.skill_evolution import register_skill_evolution_record
from src.memory.trace import MEMORY_AUDIT_EVENT_TYPE, record_memory_audit_event
from src.memory.wiki_vault import MemoryWikiVaultSyncer
from src.server.routes.chat.memory_prompt_cache import invalidate_memory_prompts
from src.storage.database import (
    get_memory_node,
    get_skill_evolution_record,
    list_run_events_by_type,
    list_skill_evolution_records,
    record_memory_retrieval_feedback,
    update_memory_node_status,
    update_skill_evolution_record_status,
)
from src.vector_store.policy import get_vector_store_policy

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
    project_id: str = Field(default="default", alias="projectId")
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


class MemoryNodeStatusUpdate(BaseModel):
    """更新历史记忆状态。"""

    status: str = Field(..., pattern="^(active|disabled|archived|deleted)$")
    reason: str = Field(default="", max_length=500)


class SkillEvolutionRecordListResponse(BaseModel):
    """Skill 自进化候选队列响应。"""

    records: list[dict[str, Any]]


class SkillEvolutionRecordAction(BaseModel):
    """Skill 自进化候选操作请求。"""

    action: str = Field(..., pattern="^(skip|restore_draft|mark_failed|register)$")
    reason: str = Field(default="", max_length=800)


class MemoryAuditEventListResponse(BaseModel):
    """Memory 审计事件列表响应。"""

    events: list[dict[str, Any]]


class MemorySettingsResponse(BaseModel):
    """Memory 设置页只读快照。"""

    vectorStore: dict[str, Any]
    admission: dict[str, Any]
    markdownPaths: dict[str, str]
    wiki: dict[str, Any]
    runtime: dict[str, Any]


class MemoryAdmissionPolicyUpdate(BaseModel):
    """更新 MemoryAdmissionGate 策略。"""

    history_threshold: float | None = Field(default=None, alias="historyThreshold", ge=0, le=1)
    markdown_threshold: float | None = Field(default=None, alias="markdownThreshold", ge=0, le=1)
    skill_threshold: float | None = Field(default=None, alias="skillThreshold", ge=0, le=1)
    min_user_chars: int | None = Field(default=None, alias="minUserChars", ge=1, le=200)
    min_assistant_chars: int | None = Field(default=None, alias="minAssistantChars", ge=1, le=500)
    reject_sensitive: bool | None = Field(default=None, alias="rejectSensitive")
    enable_redaction: bool | None = Field(default=None, alias="enableRedaction")
    enable_model_scoring: bool | None = Field(default=None, alias="enableModelScoring")
    model_score_weight: float | None = Field(default=None, alias="modelScoreWeight", ge=0, le=1)
    model_timeout_seconds: int | None = Field(default=None, alias="modelTimeoutSeconds", ge=3, le=120)
    summary_max_items_per_section: int | None = Field(default=None, alias="summaryMaxItemsPerSection", ge=1, le=50)
    summary_max_chars: int | None = Field(default=None, alias="summaryMaxChars", ge=200, le=8000)
    markdown_write_retries: int | None = Field(default=None, alias="markdownWriteRetries", ge=0, le=5)

    model_config = {"populate_by_name": True}


class MemoryRetrievalFeedback(BaseModel):
    """记录历史记忆召回结果反馈。"""

    audit_event_id: str = Field(..., alias="auditEventId", min_length=1, max_length=120)
    node_id: str = Field(..., alias="nodeId", min_length=1, max_length=120)
    project_id: str = Field(default="default", alias="projectId")
    rating: str = Field(..., pattern="^(relevant|irrelevant)$")
    reason: str = Field(default="", max_length=500)
    session_id: str = Field(default="memory-page", alias="sessionId", max_length=120)
    agent_id: str = Field(default="", alias="agentId", max_length=120)
    query: str = Field(default="", max_length=1000)

    model_config = {"populate_by_name": True}


class MemoryWikiSyncResponse(BaseModel):
    """Memory wiki vault 同步响应。"""

    path: str
    filesWritten: int
    markdownMemories: int
    historyNodes: int
    skills: int
    entities: int
    files: list[str]


class MemoryWikiGraphNodeResponse(BaseModel):
    """Memory wiki 图谱节点响应。"""

    id: str
    kind: str
    label: str
    path: str
    documents: list[str]


class MemoryWikiGraphEdgeResponse(BaseModel):
    """Memory wiki 图谱边响应。"""

    id: str
    source: str
    target: str
    type: str
    weight: int
    documents: list[str]


class MemoryWikiGraphResponse(BaseModel):
    """Memory wiki 图谱响应。"""

    nodes: list[MemoryWikiGraphNodeResponse]
    edges: list[MemoryWikiGraphEdgeResponse]
    documents: int
    historyLimit: int


def _markdown_doc_response(doc) -> dict[str, Any]:
    """统一转换 Markdown 记忆响应。"""

    return {
        "scope": doc.scope,
        "ownerId": doc.owner_id,
        "path": doc.path,
        "content": doc.content,
        "exists": doc.exists,
    }


def _wiki_sync_response(result) -> MemoryWikiSyncResponse:
    return MemoryWikiSyncResponse(
        path=result.path,
        filesWritten=result.files_written,
        markdownMemories=result.markdown_memories,
        historyNodes=result.history_nodes,
        skills=result.skills,
        entities=result.entities,
        files=result.files,
    )


def _wiki_graph_response(result) -> MemoryWikiGraphResponse:
    return MemoryWikiGraphResponse(
        nodes=[
            MemoryWikiGraphNodeResponse(
                id=node.id,
                kind=node.kind,
                label=node.label,
                path=node.path,
                documents=node.documents,
            )
            for node in result.nodes
        ],
        edges=[
            MemoryWikiGraphEdgeResponse(
                id=edge.id,
                source=edge.source,
                target=edge.target,
                type=edge.type,
                weight=edge.weight,
                documents=edge.documents,
            )
            for edge in result.edges
        ],
        documents=result.documents,
        historyLimit=result.history_limit,
    )


def _record_memory_management_audit(
    *,
    session_id: str,
    agent_id: str,
    action: str,
    status: str,
    title: str,
    content: str,
    payload: dict[str, Any],
) -> None:
    record_memory_audit_event(
        session_id=session_id or "memory-page",
        persist=True,
        agent_id=agent_id or "",
        agent_name=agent_id or "Memory Page",
        action=action,
        status=status,
        title=title,
        content=content,
        payload=payload,
    )


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
    project_id: str = Query(default="default", alias="projectId"),
    user_id: str | None = Query(default=None, alias="userId"),
    status: str = Query(default="active", pattern="^(active|disabled|archived|deleted|all)$"),
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
        status=None if status == "all" else status,
    )


@router.get("/nodes/query")
async def node_query(
    query: str = Query(default=""),
    agent_id: str | None = Query(default=None, alias="agentId"),
    date_from: str | None = Query(default=None, alias="dateFrom"),
    date_to: str | None = Query(default=None, alias="dateTo"),
    limit: int = Query(default=10, ge=1, le=100),
    workspace_id: str = Query(default="default", alias="workspaceId"),
    project_id: str = Query(default="default", alias="projectId"),
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
        workspace_id=workspace_id,
        project_id=project_id,
        user_id=user_id,
        status=None if status == "all" else status,
    )


@router.get("/nodes/{node_id}")
async def get_node(
    node_id: str,
    project_id: Annotated[str, Query(alias="projectId")] = "default",
) -> dict[str, Any]:
    """读取单个长期节点记忆。"""

    node = get_memory_node(node_id)
    if not node or str(node.get("project_id") or "default") != project_id:
        raise HTTPException(status_code=404, detail="Memory node not found")
    return node


@router.patch("/nodes/{node_id}/status")
async def update_node_status(
    node_id: str,
    data: MemoryNodeStatusUpdate,
    project_id: Annotated[str, Query(alias="projectId")] = "default",
) -> dict[str, Any]:
    """软更新历史记忆状态，并尽力同步向量索引。"""

    existing = get_memory_node(node_id)
    if not existing or str(existing.get("project_id") or "default") != project_id:
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


@router.get("/skill-evolution/records")
async def skill_evolution_records(
    status: str | None = Query(default=None, pattern="^(draft|registered|skipped|failed)$"),
    agent_id: str | None = Query(default=None, alias="agentId"),
    project_id: Annotated[str, Query(alias="projectId")] = "default",
    limit: int = Query(default=50, ge=1, le=200),
) -> SkillEvolutionRecordListResponse:
    """列出 Skill 自进化候选队列，供 Memory 页面查看中间态。"""

    records = list_skill_evolution_records(
        status=status,
        agent_id=agent_id,
        project_id=project_id,
        limit=limit,
    )
    return SkillEvolutionRecordListResponse(records=records)


@router.patch("/skill-evolution/records/{record_id}")
async def update_skill_evolution_record(
    record_id: str,
    data: SkillEvolutionRecordAction,
    project_id: Annotated[str, Query(alias="projectId")] = "default",
) -> dict[str, Any]:
    """操作 Skill 自进化候选队列。"""

    record = get_skill_evolution_record(record_id)
    if not record or str(record.get("projectId") or "default") != project_id:
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


@router.get("/audit/events")
async def memory_audit_events(
    project_id: Annotated[str, Query(alias="projectId")] = "default",
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> MemoryAuditEventListResponse:
    """列出记忆审计事件。"""

    events = list_run_events_by_type(
        MEMORY_AUDIT_EVENT_TYPE,
        project_id=project_id,
        include_payload=True,
        limit=limit,
        offset=offset,
    )
    return MemoryAuditEventListResponse(events=events)


@router.post("/retrieval/feedback")
async def record_retrieval_feedback(data: MemoryRetrievalFeedback) -> dict[str, Any]:
    """记录用户对历史记忆召回命中的相关性反馈。"""

    node = get_memory_node(data.node_id)
    if not node or str(node.get("project_id") or "default") != data.project_id:
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
    wiki_root = MemoryWikiVaultSyncer().wiki_root
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
        wiki={
            "root": str(wiki_root),
            "syncMode": "manual",
            "graphStatus": "mvp_co_occurrence_paused",
            "isRuntimeSource": False,
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


@router.post("/wiki/sync")
async def sync_memory_wiki_vault(
    history_limit: int = Query(default=200, alias="historyLimit", ge=1, le=500),
) -> MemoryWikiSyncResponse:
    """手动同步 Obsidian-compatible Memory Wiki 视图。"""

    result = MemoryWikiVaultSyncer().sync(history_limit=history_limit)
    return _wiki_sync_response(result)


@router.get("/wiki/graph")
async def get_memory_wiki_graph(
    history_limit: int = Query(default=200, alias="historyLimit", ge=1, le=500),
    edge_limit: int = Query(default=300, alias="edgeLimit", ge=1, le=1000),
) -> MemoryWikiGraphResponse:
    """返回 Memory Wiki 结构化实体图谱，不改变权威记忆源。"""

    result = MemoryWikiVaultSyncer().graph(history_limit=history_limit, edge_limit=edge_limit)
    return _wiki_graph_response(result)


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

    doc = MarkdownMemoryStore().read_global_memory("default")
    return _markdown_doc_response(doc)


@router.put("/markdown/global")
async def write_default_global_markdown_memory(data: MarkdownMemoryUpdate) -> dict[str, Any]:
    """保存唯一全局偏好 Markdown 记忆；首次保存时创建文件。"""

    doc = MarkdownMemoryStore().write_global_memory("default", data.content)
    invalidate_memory_prompts()
    return _markdown_doc_response(doc)


@router.get("/markdown/global/{workspace_id}")
async def read_global_markdown_memory(workspace_id: str = "default") -> dict[str, Any]:
    """读取全局偏好 Markdown 记忆；不存在时返回默认模板但不创建文件。"""

    doc = MarkdownMemoryStore().read_global_memory(workspace_id)
    return _markdown_doc_response(doc)


@router.put("/markdown/global/{workspace_id}")
async def write_global_markdown_memory(workspace_id: str, data: MarkdownMemoryUpdate) -> dict[str, Any]:
    """保存全局偏好 Markdown 记忆；首次保存时创建文件。"""

    doc = MarkdownMemoryStore().write_global_memory(workspace_id, data.content)
    invalidate_memory_prompts()
    return _markdown_doc_response(doc)
