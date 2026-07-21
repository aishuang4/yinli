"""对话路由的 Pydantic 模型、常量与进程内共享状态。

注意：PENDING_INTERACTIONS / PENDING_AGENTS 是 send 与 resume 端点共享的
模块级可变 dict，必须保持是同一对象，所有使用方都从本模块 import。
"""

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

from src.config.runtime_paths import DATA_ROOT

CHAT_DATA_PATH = DATA_ROOT / "chat_sessions.json"
CHAT_NAMESPACE = "chat_store"
CHAT_STORE_KEY = "default"
MULTIMODAL_MODELS = {"qwen3.7-plus"}
HITL_EVENT_TYPES = {"REQUIRE_USER_CONFIRM", "REQUIRE_EXTERNAL_EXECUTION"}
TRACE_EVENT_TYPES = {
    "REPLY_START",
    "REPLY_END",
    "MODEL_CALL_START",
    "MODEL_CALL_END",
    "TOOL_CALL_START",
    "TOOL_CALL_END",
    "TOOL_RESULT_START",
    "TOOL_RESULT_END",
}
PENDING_INTERACTIONS: dict[str, "PendingInteraction"] = {}
PENDING_AGENTS: dict[str, object] = {}
PENDING_SUBAGENT_PARENT_CONTEXTS: dict[str, dict[str, Any]] = {}


def _empty_display_snapshot() -> dict[str, Any]:
    """创建空的 Chat 展示快照。"""

    return {"version": 3, "mode": "single_agent", "actors": [], "blocks": []}


class ChatImagePayload(BaseModel):
    """用户上传图片"""

    name: str = ""
    media_type: str
    data: str


class ChatFilePayload(BaseModel):
    """已保存到当前 Project 的普通附件。"""

    name: str
    path: str
    size: int = 0
    media_type: str = Field(default="application/octet-stream", alias="mediaType")

    model_config = {"populate_by_name": True}


class ChatSkillRun(BaseModel):
    """本轮对话使用的 Skill 记录"""

    id: str
    name: str
    description: str = ""
    trigger: str = ""
    content: str = ""
    resource_path: str = Field(default="", alias="resourcePath")
    resources: list[str] = Field(default_factory=list)
    status: str = "running"

    model_config = {"populate_by_name": True}


class ChatToolRun(BaseModel):
    """工具调用记录"""

    id: str
    name: str
    args: str = ""
    result: str = ""
    status: str = "calling"


class ChatModelCacheRun(BaseModel):
    """模型厂商侧 Prompt Cache 统计。"""

    id: str
    model_name: str = Field(default="", alias="modelName")
    input_tokens: int = Field(default=0, alias="inputTokens")
    output_tokens: int = Field(default=0, alias="outputTokens")
    cache_creation_input_tokens: int = Field(default=0, alias="cacheCreationInputTokens")
    cache_input_tokens: int = Field(default=0, alias="cacheInputTokens")
    cache_hit_rate: float = Field(default=0, alias="cacheHitRate")
    estimated_saved_input_tokens: int = Field(default=0, alias="estimatedSavedInputTokens")
    created_at: str = Field(alias="createdAt")

    model_config = {"populate_by_name": True}


class ChatPlanStep(BaseModel):
    """结构化计划步骤。"""

    id: str
    plan_id: str = Field(alias="planId")
    step_index: int = Field(alias="stepIndex")
    title: str
    description: str = ""
    status: str = "pending"
    assigned_agent_id: str | None = Field(default=None, alias="assignedAgentId")
    evidence: str = ""
    result: str = ""
    started_at: str | None = Field(default=None, alias="startedAt")
    completed_at: str | None = Field(default=None, alias="completedAt")
    updated_at: str = Field(alias="updatedAt")

    model_config = {"populate_by_name": True}


class ChatPlanRun(BaseModel):
    """Plan Mode/历史计划记录。"""

    id: str
    run_id: str = Field(default="", alias="runId")
    session_id: str = Field(alias="sessionId")
    agent_id: str = Field(alias="agentId")
    title: str
    goal: str
    status: str
    markdown: str
    current_step_id: str | None = Field(default=None, alias="currentStepId")
    steps: list[ChatPlanStep] = Field(default_factory=list)
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")
    completed_at: str | None = Field(default=None, alias="completedAt")

    model_config = {"populate_by_name": True}


class StoredChatMessage(BaseModel):
    """持久化对话消息"""

    id: str
    role: str
    content: str
    agent_id: str | None = None
    agent_name: str | None = None
    model_name: str | None = None
    error: str | None = None
    thinking: str = ""
    images: list[ChatImagePayload] = Field(default_factory=list)
    files: list[ChatFilePayload] = Field(default_factory=list)
    tool_calls: list[ChatToolRun] = Field(default_factory=list, alias="toolCalls")
    skill_calls: list[ChatSkillRun] = Field(default_factory=list, alias="skillCalls")
    model_caches: list[ChatModelCacheRun] = Field(default_factory=list, alias="modelCaches")
    runtime_events: list[dict] = Field(default_factory=list, alias="runtimeEvents")
    plan_runs: list[ChatPlanRun] = Field(default_factory=list, alias="planRuns")
    display_snapshot: dict[str, Any] = Field(default_factory=_empty_display_snapshot, alias="displaySnapshot")
    team_snapshot: dict[str, Any] | None = Field(default=None, alias="teamSnapshot")
    clarification_requests: list[dict] = Field(default_factory=list, alias="clarificationRequests")
    clarification_resolved: bool = Field(default=False, alias="clarificationResolved")
    created_at: str

    model_config = {"populate_by_name": True}


class ChatSession(BaseModel):
    """对话会话摘要"""

    id: str
    title: str
    agent_id: str
    agent_name: str
    mode: Literal["agent", "team"] = "agent"
    workspace_id: str = "default"
    project_id: str = "default"
    created_at: str
    updated_at: str
    message_count: int = 0


class ChatStore(BaseModel):
    """对话持久化文件结构"""

    sessions: list[ChatSession] = Field(default_factory=list)
    messages: dict[str, list[StoredChatMessage]] = Field(default_factory=dict)


class PendingInteraction(BaseModel):
    """In-process pending human interaction for AgentScope resume."""

    id: str
    type: str
    session_id: str
    agent_id: str
    agent_name: str
    model_name: str
    reply_id: str
    run_id: str = ""
    tool_calls: list[dict]
    resume_tool_calls: list[dict] | None = None
    message: str
    assistant_text: str = ""
    assistant_tool_calls: list[ChatToolRun] = Field(default_factory=list)
    skill_runs: list[ChatSkillRun] = Field(default_factory=list)
    scope: str = "single"
    chat_data: dict | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    model_config = {"arbitrary_types_allowed": True}


class ChatResumeRequest(BaseModel):
    """Resume a paused Agent run after human interaction."""

    interaction_id: str = Field(alias="interactionId")
    action: str
    external_results: dict[str, str] = Field(default_factory=dict, alias="externalResults")
    remember_approval: bool = Field(default=False, alias="rememberApproval")
    """确认后在当前会话内自动允许同类工具，不跨会话持久化。"""

    model_config = {"populate_by_name": True}


class ChatMessage(BaseModel):
    """对话消息请求体"""

    session_id: str | None = None
    """会话 ID（可选，用于上下文连续性）"""

    project_id: str | None = None
    """Project ID；未提供时使用 default Project。"""

    message: str
    """用户输入的消息文本"""

    display_message: str | None = None
    """前端历史展示用文本；为空时使用 message"""

    agent_id: str | None = None
    """目标 Agent ID（可选，默认使用 default Agent）"""

    agent_name: str | None = None
    """Agent 名称（可选）"""

    mode: Literal["agent", "team"] = "agent"
    """执行模式；Team 模式下所选 Agent 是本轮 Leader。"""

    system_prompt: str | None = None
    """Agent 系统提示词（可选，用于临时自定义）"""

    model: str | None = None
    """指定模型（可选，覆盖默认配置）"""

    skill_ids: list[str] = Field(default_factory=list)
    """当前 Agent 绑定的 Skill ID 列表"""

    deep_thinking: bool = False
    """是否启用深度思考"""

    images: list[ChatImagePayload] = Field(default_factory=list)
    """图片输入（base64，不含 data URL 前缀）"""

    files: list[ChatFilePayload] = Field(default_factory=list)
    """已上传到当前 Project 的普通附件。"""

    sandbox: bool = False
    """沙盒运行：不写入历史对话"""

    model_config = {"populate_by_name": True, "extra": "forbid"}
