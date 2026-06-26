"""对话路由的 Pydantic 模型、常量与进程内共享状态。

注意：PENDING_INTERACTIONS / PENDING_AGENTS 是 send 与 resume 端点共享的
模块级可变 dict，必须保持是同一对象，所有使用方都从本模块 import。
"""

from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field


CHAT_DATA_PATH = Path("data/chat_sessions.json")
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


class ChatImagePayload(BaseModel):
    """用户上传图片"""

    name: str = ""
    media_type: str
    data: str


class ChatTeamMemberPayload(BaseModel):
    """前端传入的 Team 成员摘要"""

    id: str = ""
    agent_id: str = Field(alias="agentId")
    name: str
    role: str
    description: str = ""
    execution_mode: str = Field(default="parallel", alias="executionMode")
    depends_on: list[str] = Field(default_factory=list, alias="dependsOn")
    handoff_prompt: str = Field(default="", alias="handoffPrompt")

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
    """Plan Runtime 记录。"""

    id: str
    run_id: str = Field(default="", alias="runId")
    session_id: str = Field(alias="sessionId")
    agent_id: str = Field(alias="agentId")
    team_id: str | None = Field(default=None, alias="teamId")
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


class ChatTeamActivity(BaseModel):
    """Team 协作活动记录"""

    id: str
    agent_id: str = Field(alias="agentId")
    agent_name: str = Field(alias="agentName")
    role: str
    status: str
    task: str = ""
    detail: str = ""
    output: str = ""
    thinking: str = ""
    tool_calls: list[ChatToolRun] = Field(default_factory=list, alias="toolCalls")
    execution_mode: str = Field(default="parallel", alias="executionMode")
    depends_on: list[str] = Field(default_factory=list, alias="dependsOn")
    wait_reason: str = Field(default="", alias="waitReason")
    dependency_output_count: int = Field(default=0, alias="dependencyOutputCount")
    leader_context_count: int = Field(default=0, alias="leaderContextCount")
    created_at: str = Field(alias="createdAt")

    model_config = {"populate_by_name": True}


class ChatSubagentCall(BaseModel):
    """子 Agent 调用记录"""

    id: str
    name: str
    args: str = ""
    result: str = ""
    status: str = "completed"

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
    tool_calls: list[ChatToolRun] = Field(default_factory=list, alias="toolCalls")
    subagent_calls: list[ChatSubagentCall] = Field(default_factory=list, alias="subagentCalls")
    skill_calls: list[ChatSkillRun] = Field(default_factory=list, alias="skillCalls")
    team_activities: list[ChatTeamActivity] = Field(default_factory=list, alias="teamActivities")
    model_caches: list[ChatModelCacheRun] = Field(default_factory=list, alias="modelCaches")
    runtime_events: list[dict] = Field(default_factory=list, alias="runtimeEvents")
    plan_runs: list[ChatPlanRun] = Field(default_factory=list, alias="planRuns")
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
    team_id: str | None = None
    team_name: str | None = None
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
    message: str
    assistant_text: str = ""
    assistant_tool_calls: list[ChatToolRun] = Field(default_factory=list)
    skill_runs: list[ChatSkillRun] = Field(default_factory=list)
    team_activities: list[ChatTeamActivity] = Field(default_factory=list)
    scope: str = "leader"
    chat_data: dict | None = None
    leader_system_prompt: str = ""
    worker_member: dict | None = None
    worker_text: str = ""
    worker_thinking: str = ""
    worker_tool_calls: list[ChatToolRun] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    model_config = {"arbitrary_types_allowed": True}


class ChatResumeRequest(BaseModel):
    """Resume a paused Agent run after human interaction."""

    interaction_id: str = Field(alias="interactionId")
    action: str
    external_results: dict[str, str] = Field(default_factory=dict, alias="externalResults")

    model_config = {"populate_by_name": True}


class ChatMessage(BaseModel):
    """对话消息请求体"""

    session_id: str | None = None
    """会话 ID（可选，用于上下文连续性）"""

    message: str
    """用户输入的消息文本"""

    display_message: str | None = None
    """前端历史展示用文本；为空时使用 message"""

    agent_id: str | None = None
    """目标 Agent ID（可选，默认使用 default Agent）"""

    agent_name: str | None = None
    """Agent 名称（可选）"""

    team_id: str | None = None
    """Team ID（Team 模式下传入）"""

    team_name: str | None = None
    """Team 名称（Team 模式下传入）"""

    team_members: list[ChatTeamMemberPayload] = Field(default_factory=list)
    """Team 成员摘要（Team 模式下用于注入 Leader 上下文）"""

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

    sandbox: bool = False
    """沙盒运行：不写入历史对话"""

    model_config = {"populate_by_name": True}


class TeamWorkerSelectionRequest(BaseModel):
    """Ask a model to preselect relevant Team workers for one user task."""

    message: str
    agent_id: str | None = Field(default=None, alias="agentId")
    agent_name: str | None = Field(default=None, alias="agentName")
    team_name: str | None = Field(default=None, alias="teamName")
    team_members: list[ChatTeamMemberPayload] = Field(default_factory=list, alias="teamMembers")
    model: str | None = None

    model_config = {"populate_by_name": True}


class TeamWorkerSelectionResponse(BaseModel):
    selected_agent_ids: list[str] = Field(alias="selectedAgentIds")
    reason: str = ""

    model_config = {"populate_by_name": True}
