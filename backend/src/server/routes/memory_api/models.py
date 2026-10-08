"""Memory API 的请求和响应模型。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

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


class MarkdownCandidateListResponse(BaseModel):
    """待确认 Markdown 记忆候选队列响应。"""

    records: list[dict[str, Any]]
    pendingCount: int


class MarkdownCandidateAction(BaseModel):
    """待确认候选操作请求。"""

    action: str = Field(..., pattern="^(accept|reject)$")
    reason: str = Field(default="", max_length=800)

