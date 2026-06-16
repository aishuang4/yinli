"""
对话路由 — 对接 AgentScope 2.0 真实 Agent

提供对话相关的 API 端点：
- POST /api/chat/send    发送消息并通过 SSE 返回 Agent 事件流
- GET  /api/chat/sessions 获取会话列表

事件流格式遵循 AgentScope 2.0 EventType：
  REPLY_START / REPLY_END
  MODEL_CALL_START / MODEL_CALL_END
  TEXT_BLOCK_START / TEXT_BLOCK_DELTA / TEXT_BLOCK_END
  THINKING_BLOCK_START / THINKING_BLOCK_DELTA / THINKING_BLOCK_END
  TOOL_CALL_START / TOOL_CALL_DELTA / TOOL_CALL_END
  TOOL_RESULT_START / TOOL_RESULT_TEXT_DELTA / TOOL_RESULT_END
"""

import json
import asyncio
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncGenerator
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from agentscope.event import (
    ConfirmResult,
    ExternalExecutionResultEvent,
    UserConfirmResultEvent,
)
from agentscope.message import (
    Base64Source,
    DataBlock,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
    ToolResultState,
    UserMsg,
)
from agentscope.permission import PermissionMode

from src.agents.factory import (
    AgentGuardrails,
    AgentModelOptions,
    KnowledgeToolRuntimeSpec,
    McpRuntimeSpec,
    get_or_create_agent,
)
from src.config.settings import settings
from src.config.runtime_settings import MODEL_PROVIDERS, get_effective_model_settings
from src.server.routes.agents import get_registered_agent
from src.server.routes.memory import (
    append_agent_memory_event,
    build_agent_memory_system_prompt,
    maybe_summarize_agent_run,
)
from src.server.routes.mcp import resolve_mcp_runtime_servers
from src.server.routes.tools import get_enabled_knowledge_tools
from src.server.routes.skills import (
    build_skill_system_prompt_from_skills,
    list_skill_usage_details,
    select_relevant_skills,
)
from src.storage.database import (
    append_run_event,
    get_json_record,
    load_chat_store,
    migrate_json_object_file,
    save_chat_store,
)

router = APIRouter()

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


class StoredChatMessage(BaseModel):
    """持久化对话消息"""

    id: str
    role: str
    content: str
    agent_id: str | None = None
    agent_name: str | None = None
    model_name: str | None = None
    error: str | None = None
    images: list[ChatImagePayload] = Field(default_factory=list)
    tool_calls: list[ChatToolRun] = Field(default_factory=list, alias="toolCalls")
    skill_calls: list[ChatSkillRun] = Field(default_factory=list, alias="skillCalls")
    team_activities: list[ChatTeamActivity] = Field(default_factory=list, alias="teamActivities")
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
    tool_calls: list[dict]
    message: str
    assistant_text: str = ""
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
    """沙盒运行：不写入历史对话和 Agent 记忆"""


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


@router.post("/team-worker-selection", response_model=TeamWorkerSelectionResponse)
async def select_team_workers(data: TeamWorkerSelectionRequest) -> TeamWorkerSelectionResponse:
    """Use the leader model to preselect relevant workers for a single Team run."""

    workers = [
        member
        for member in data.team_members
        if member.role != "leader" and member.agent_id != data.agent_id
    ]
    if not workers:
        return TeamWorkerSelectionResponse(selectedAgentIds=[], reason="当前 Team 没有可选择的 Worker。")

    runtime_model_settings = get_effective_model_settings()
    model_name = data.model or runtime_model_settings.default_model
    agent = get_or_create_agent(
        agent_id=f"team-worker-selector:{uuid.uuid4().hex}",
        name="TeamWorkerSelector",
        system_prompt="\n".join([
            "You select which worker agents should participate in one Team run.",
            "Return only a compact JSON object. Do not call tools. Do not write markdown.",
            'Schema: {"selectedAgentIds":["agent-id"],"reason":"short reason"}',
            "Select only workers that are materially relevant to the user task.",
            "If the task is simple and no worker is needed, return an empty selectedAgentIds array.",
            "If a selected worker depends on another worker, include the dependency worker too.",
        ]),
        model_name=model_name,
        permission_mode=PermissionMode.BYPASS,
    )
    worker_lines = []
    for member in workers:
        dependencies = ", ".join(member.depends_on) if member.depends_on else "none"
        worker_lines.append(
            f"- agentId={member.agent_id}; name={member.name}; role={member.role}; "
            f"description={member.description or 'none'}; executionMode={member.execution_mode}; "
            f"dependsOn={dependencies}; handoffPrompt={member.handoff_prompt or 'none'}"
        )
    prompt = "\n\n".join([
        f"Team: {data.team_name or 'Unnamed Team'}",
        "User task:",
        data.message,
        "Available workers:",
        "\n".join(worker_lines),
    ])

    text = ""
    async for event in agent.reply_stream(UserMsg(name="user", content=prompt)):
        event_data = event.model_dump(mode="json")
        event_type = event_data.pop("type", "UNKNOWN")
        if event_type == "TEXT_BLOCK_DELTA":
            text += str(event_data.get("delta", ""))

    selected, reason = _parse_worker_selection(text, {member.agent_id for member in workers})
    selected = _expand_selected_workers_with_dependencies(selected, workers)
    return TeamWorkerSelectionResponse(selectedAgentIds=selected, reason=reason)


@router.post("/send")
async def send_message(data: ChatMessage) -> StreamingResponse:
    """
    发送消息并通过 SSE 返回 Agent 事件流

    调用流程：
    1. 获取或创建 Agent 实例（通过 factory）
    2. 构建 UserMsg 并调用 agent.reply_stream()
    3. 将 AgentScope 事件序列化为 SSE 格式推送到前端

    Args:
        data: 包含 session_id、message、agent_id 等的请求体

    Returns:
        StreamingResponse: SSE 事件流（text/event-stream）
    """

    async def event_stream() -> AsyncGenerator[str, None]:
        """
        生成 SSE 事件流

        将 AgentScope 2.0 的 AgentEvent 转换为 SSE 格式：
          event: <EventType>
          data: <JSON>
        """
        session_id = data.session_id or uuid.uuid4().hex
        agent_id = data.agent_id or "default"
        agent_config = get_registered_agent(agent_id)
        agent_name = data.agent_name or (agent_config.name if agent_config else "Assistant")
        base_system_prompt = (
            data.system_prompt
            or (agent_config.system_prompt if agent_config else None)
            or "You are a helpful AI assistant."
        )
        runtime_context_prompt = _build_runtime_context_prompt()
        plan_system_prompt = _build_agent_plan_prompt(agent_config)
        memory_system_prompt = _build_agent_memory_prompt(agent_id)
        history_system_prompt = _build_conversation_history_prompt(session_id)
        team_system_prompt = _build_team_system_prompt(data.team_name, data.team_members)
        skill_ids = agent_config.skills if agent_config and not data.skill_ids else data.skill_ids
        used_skills = select_relevant_skills(skill_ids, data.message)
        skill_system_prompt = build_skill_system_prompt_from_skills(used_skills)
        clarification_system_prompt = _build_clarification_system_prompt()
        used_skill_details = list_skill_usage_details(used_skills)
        skill_runs = [
            ChatSkillRun.model_validate({**skill_detail, "status": "running"})
            for skill_detail in used_skill_details
        ]
        system_prompt = "\n\n".join(
            part
            for part in [
                base_system_prompt,
                runtime_context_prompt,
                plan_system_prompt,
                memory_system_prompt,
                history_system_prompt,
                team_system_prompt,
                skill_system_prompt,
                clarification_system_prompt,
            ]
            if part
        )
        runtime_model_settings = get_effective_model_settings()
        model_config_data = agent_config.model_config_data if agent_config else None
        model_name = (
            data.model
            or (model_config_data.primary if model_config_data else None)
            or runtime_model_settings.default_model
        )
        fallback_names = model_config_data.fallback if model_config_data else runtime_model_settings.fallback_models
        model_options = AgentModelOptions(
            temperature=model_config_data.temperature if model_config_data else None,
            top_p=model_config_data.top_p if model_config_data else None,
            max_tokens=model_config_data.max_tokens if model_config_data else None,
        )
        guardrails = _build_runtime_guardrails(agent_config)
        mcp_servers = _build_runtime_mcp_servers(agent_config)
        knowledge_tools = _build_runtime_knowledge_tools(agent_config)
        assistant_text = ""
        assistant_thinking = ""
        assistant_error: str | None = None
        worker_outputs: list[dict[str, str]] = []
        persist_run = not data.sandbox
        team_activities = _build_team_activities(
            team_name=data.team_name,
            leader_agent_id=agent_id,
            leader_agent_name=agent_name,
            members=data.team_members,
            user_message=data.message,
        )

        if persist_run:
            _append_user_message(
                session_id=session_id,
                agent_id=agent_id,
                agent_name=agent_name,
                team_id=data.team_id,
                team_name=data.team_name,
                model_name=model_name,
                content=data.message,
                images=data.images,
            )
            append_agent_memory_event(
                agent_id=agent_id,
                event_type="user_message",
                title=_make_session_title(data.message),
                content=data.message,
                session_id=session_id,
                metadata={
                    "agentName": agent_name,
                    "teamId": data.team_id,
                    "teamName": data.team_name,
                    "model": model_name,
                    "imageCount": len(data.images),
                    "sandbox": data.sandbox,
                },
            )
            _record_run_event(
                session_id=session_id,
                event_type="USER_MESSAGE",
                persist=persist_run,
                agent_id=agent_id,
                agent_name=agent_name,
                status="completed",
                title="User message",
                content=data.message,
                payload={
                    "teamId": data.team_id,
                    "teamName": data.team_name,
                    "model": model_name,
                    "imageCount": len(data.images),
                },
            )

        yield _sse_event("SESSION", {
            "session_id": session_id,
        })
        _record_run_event(
            session_id=session_id,
            event_type="SESSION",
            persist=persist_run,
            agent_id=agent_id,
            agent_name=agent_name,
            status="running",
            title="Run started",
            payload={
                "teamId": data.team_id,
                "teamName": data.team_name,
                "model": model_name,
                "sandbox": data.sandbox,
                "workspace": _workspace_trace_payload(guardrails),
            },
        )
        _record_workspace_event(
            session_id=session_id,
            persist=persist_run,
            scope="leader",
            agent_id=agent_id,
            agent_name=agent_name,
            guardrails=guardrails,
        )

        if team_activities:
            yield _sse_event("TEAM_ACTIVITY", {
                "activities": [item.model_dump(by_alias=True) for item in team_activities],
            })
            _record_run_event(
                session_id=session_id,
                event_type="TEAM_ACTIVITY",
                persist=persist_run,
                agent_id=agent_id,
                agent_name=agent_name,
                status="running",
                title="Team initialized",
                payload={"activities": [item.model_dump(by_alias=True) for item in team_activities]},
            )

        if used_skill_details:
            yield _sse_event("SKILL_CONTEXT", {
                "skills": used_skill_details,
            })
            _record_run_event(
                session_id=session_id,
                event_type="SKILL_CONTEXT",
                persist=persist_run,
                agent_id=agent_id,
                agent_name=agent_name,
                status="running",
                title="Skills injected",
                payload={"skills": used_skill_details},
            )
            for skill_detail in used_skill_details:
                if persist_run:
                    append_agent_memory_event(
                        agent_id=agent_id,
                        event_type="skill_use",
                        title=f"使用 Skill: {skill_detail.get('name', 'Skill')}",
                        content=str(skill_detail.get("content", "")),
                        session_id=session_id,
                        metadata={
                            "skillId": skill_detail.get("id"),
                            "skillName": skill_detail.get("name"),
                        },
                    )

        if data.images and model_name not in MULTIMODAL_MODELS:
            assistant_error = f"{model_name} 不支持图片输入，请切换到 qwen3.7-plus。"
            failed_skill_runs = _set_skill_run_status(skill_runs, "failed")
            if persist_run:
                _append_assistant_message(
                    session_id=session_id,
                    agent_id=agent_id,
                    agent_name=agent_name,
                    model_name=model_name,
                    content="",
                    error=assistant_error,
                    skill_calls=failed_skill_runs,
                    team_activities=_set_team_activity_status(team_activities, "failed"),
                )
            if team_activities:
                yield _sse_event("TEAM_ACTIVITY", {
                    "activities": [
                        item.model_dump(by_alias=True)
                        for item in _set_team_activity_status(team_activities, "failed")
                    ],
                })
            if failed_skill_runs:
                yield _sse_event("SKILL_END", {
                    "skills": [item.model_dump(by_alias=True) for item in failed_skill_runs],
                    "status": "failed",
                })
            yield _sse_event("ERROR", {
                "message": assistant_error,
            })
            return

        # 检查 API Key 是否配置
        provider = MODEL_PROVIDERS.get(runtime_model_settings.provider)
        if provider and provider.requires_api_key and not runtime_model_settings.api_key:
            assistant_error = f"{provider.name} API Key 未配置。请在系统设置中配置模型供应商和 API Key。"
            failed_skill_runs = _set_skill_run_status(skill_runs, "failed")
            if persist_run:
                _append_assistant_message(
                    session_id=session_id,
                    agent_id=agent_id,
                    agent_name=agent_name,
                    model_name=model_name,
                    content="",
                    error=assistant_error,
                    skill_calls=failed_skill_runs,
                    team_activities=_set_team_activity_status(team_activities, "failed"),
                )
            if team_activities:
                yield _sse_event("TEAM_ACTIVITY", {
                    "activities": [
                        item.model_dump(by_alias=True)
                        for item in _set_team_activity_status(team_activities, "failed")
                    ],
                })
            if failed_skill_runs:
                yield _sse_event("SKILL_END", {
                    "skills": [item.model_dump(by_alias=True) for item in failed_skill_runs],
                    "status": "failed",
                })
            yield _sse_event("ERROR", {
                "message": assistant_error,
            })
            return

        # 获取或创建 Agent 实例
        try:
            if team_activities:
                async for worker_event in _run_team_workers(
                    data=data,
                    session_id=session_id,
                    team_activities=team_activities,
                    model_name=model_name,
                    leader_system_prompt=system_prompt,
                    skill_runs=skill_runs,
                ):
                    yield worker_event
                if any(activity.status == "waiting" for activity in team_activities):
                    return
            worker_outputs = _collect_worker_outputs(team_activities)
            if worker_outputs:
                system_prompt = "\n\n".join(
                    part
                    for part in [
                        system_prompt,
                        _build_worker_outputs_system_prompt(worker_outputs),
                    ]
                    if part
                )
                leader_activity = _find_team_activity(team_activities, agent_id)
                if leader_activity:
                    _replace_team_activity(
                        team_activities,
                        leader_activity.model_copy(update={
                            "status": "running",
                            "detail": f"Worker 已完成，Leader 正在基于 {len(worker_outputs)} 个 Worker 输出生成最终回复。",
                            "leader_context_count": len(worker_outputs),
                        }),
                    )
                    yield _sse_event("TEAM_ACTIVITY", {
                        "activities": [item.model_dump(by_alias=True) for item in team_activities],
                    })

            skill_cache_key = ",".join(sorted(skill.id for skill in used_skills))
            fallback_cache_key = ",".join(fallback_names)
            mcp_cache_key = ",".join(
                f"{server.name}:{','.join(server.enable_tools)}"
                for server in mcp_servers
            )
            knowledge_cache_key = ",".join(tool.id for tool in knowledge_tools)
            guardrail_cache_key = (
                f"scope-{guardrails.filesystem_scope}:"
                f"net-{guardrails.network_enabled}:"
                f"hitl-{guardrails.human_approval}:"
                f"tool-permission-{guardrails.tool_permission_mode}:"
                f"tool-profile-{guardrails.tool_permission_profile}:"
                f"exec-{guardrails.execution_backend}:"
                f"docker-{guardrails.docker_image}:"
                f"persist-sandbox-{guardrails.persistent_sandbox}:"
                f"timeout-{guardrails.timeout_seconds}"
            )
            runtime_agent_id = (
                f"{agent_id}:{session_id}:{model_name}:thinking-{data.deep_thinking}:"
                f"fallbacks-{fallback_cache_key}:"
                f"skills-{skill_cache_key}:mcp-{mcp_cache_key}:knowledge-{knowledge_cache_key}:"
                f"guardrails-{guardrail_cache_key}"
            )
            agent = get_or_create_agent(
                agent_id=runtime_agent_id,
                name=agent_name,
                system_prompt=system_prompt,
                model_name=model_name,
                fallback_names=fallback_names,
                thinking_enable=data.deep_thinking,
                permission_mode=_permission_mode_for_guardrails(guardrails),
                model_options=model_options,
                guardrails=guardrails,
                mcp_servers=mcp_servers,
                knowledge_tools=knowledge_tools,
                on_fallback_event=_make_fallback_trace_recorder(
                    session_id=session_id,
                    persist=persist_run,
                    scope="leader",
                    agent_id=agent_id,
                    agent_name=agent_name,
                ),
            )
        except Exception as e:
            assistant_error = f"Agent 创建失败: {str(e)}"
            failed_skill_runs = _set_skill_run_status(skill_runs, "failed")
            if persist_run:
                _append_assistant_message(
                    session_id=session_id,
                    agent_id=agent_id,
                    agent_name=agent_name,
                    model_name=model_name,
                    content="",
                    error=assistant_error,
                    skill_calls=failed_skill_runs,
                    team_activities=_set_team_activity_status(team_activities, "failed"),
                )
            if team_activities:
                yield _sse_event("TEAM_ACTIVITY", {
                    "activities": [
                        item.model_dump(by_alias=True)
                        for item in _set_team_activity_status(team_activities, "failed")
                    ],
                })
            if failed_skill_runs:
                yield _sse_event("SKILL_END", {
                    "skills": [item.model_dump(by_alias=True) for item in failed_skill_runs],
                    "status": "failed",
                })
            yield _sse_event("ERROR", {
                "message": assistant_error,
            })
            return

        # 构建用户消息
        user_msg = UserMsg(
            name="user",
            content=_build_user_content(
                _build_leader_user_message(data.message, worker_outputs),
                data.images,
            ),
        )

        # 调用 AgentScope 真实 Agent 的 reply_stream
        try:
            assistant_tool_calls: list[ChatToolRun] = []
            async for event in agent.reply_stream(user_msg):
                # 将 Pydantic 事件模型序列化为 dict
                event_data = event.model_dump(mode="json")

                # 提取事件类型（EventType 枚举值）
                event_type = event_data.pop("type", "UNKNOWN")
                _attach_provider_cache_usage(event_type, event_data)
                _record_agent_scope_event(
                    session_id=session_id,
                    event_type=event_type,
                    event_data=event_data,
                    persist=persist_run,
                    scope="leader",
                    agent_id=agent_id,
                    agent_name=agent_name,
                )
                next_tool_calls = _update_worker_tool_calls(
                    assistant_tool_calls,
                    event_type,
                    event_data,
                )
                if next_tool_calls is not assistant_tool_calls:
                    assistant_tool_calls = next_tool_calls
                if event_type in HITL_EVENT_TYPES:
                    interaction = _create_pending_interaction(
                        interaction_type=event_type,
                        agent=agent,
                        session_id=session_id,
                        agent_id=agent_id,
                        agent_name=agent_name,
                        model_name=model_name,
                        reply_id=str(event_data.get("reply_id") or ""),
                        tool_calls=list(event_data.get("tool_calls") or []),
                        message=data.message,
                        assistant_text=assistant_text,
                        skill_runs=skill_runs,
                        team_activities=team_activities,
                        persist=persist_run,
                    )
                    yield _sse_event("HITL_REQUIRED", _hitl_payload(interaction))
                    return
                if event_type == "TEXT_BLOCK_DELTA":
                    assistant_text += str(event_data.get("delta", ""))
                elif event_type == "THINKING_BLOCK_DELTA":
                    assistant_thinking += str(event_data.get("delta", ""))

                if event_type == "REPLY_END" and skill_runs:
                    completed_skill_runs = _set_skill_run_status(skill_runs, "completed")
                    yield _sse_event("SKILL_END", {
                        "skills": [item.model_dump(by_alias=True) for item in completed_skill_runs],
                        "status": "completed",
                    })

                # 发送 SSE 事件
                yield _sse_event(event_type, event_data)

            clarification = _extract_clarification_request(assistant_text) or _extract_clarification_request(assistant_thinking)
            if clarification:
                _mark_team_leader_waiting_for_clarification(team_activities, agent_id)
                if team_activities:
                    yield _sse_event("TEAM_ACTIVITY", {
                        "activities": [item.model_dump(by_alias=True) for item in team_activities],
                    })
                _record_run_event(
                    session_id=session_id,
                    event_type="CLARIFICATION_REQUIRED",
                    persist=persist_run,
                    scope="leader",
                    agent_id=agent_id,
                    agent_name=agent_name,
                    status="waiting",
                    title=str(clarification.get("title") or "Clarification required"),
                    payload=clarification,
                )
                yield _sse_event("CLARIFICATION_REQUIRED", clarification)
                return

            completed_skill_runs = _set_skill_run_status(skill_runs, "completed")
            completed_team_activities = _finalize_team_activities(
                team_activities,
                leader_agent_id=agent_id,
                leader_output=assistant_text,
            )

            if persist_run:
                _append_assistant_message(
                    session_id=session_id,
                    agent_id=agent_id,
                    agent_name=agent_name,
                    model_name=model_name,
                    content=assistant_text,
                    error=assistant_error,
                    skill_calls=completed_skill_runs,
                    tool_calls=assistant_tool_calls,
                    team_activities=completed_team_activities,
                )
                _record_run_event(
                    session_id=session_id,
                    event_type="ASSISTANT_MESSAGE",
                    persist=persist_run,
                    scope="leader",
                    agent_id=agent_id,
                    agent_name=agent_name,
                    status="completed" if not assistant_error else "failed",
                    title="Assistant response",
                    content=_trim_text(assistant_text, 1200),
                    payload={
                        "model": model_name,
                        "toolCallCount": len(assistant_tool_calls),
                        "skillIds": [item.id for item in completed_skill_runs],
                    },
                )
            if team_activities:
                yield _sse_event("TEAM_ACTIVITY", {
                    "activities": [
                        item.model_dump(by_alias=True)
                        for item in completed_team_activities
                    ],
                })
            if persist_run:
                append_agent_memory_event(
                    agent_id=agent_id,
                    event_type="assistant_message",
                    title="Assistant 回复",
                    content=assistant_text,
                    session_id=session_id,
                    metadata={
                        "agentName": agent_name,
                        "model": model_name,
                        "skillIds": [item.id for item in completed_skill_runs],
                    },
                )
                memory_facts = await maybe_summarize_agent_run(
                    agent_id=agent_id,
                    agent_name=agent_name,
                    session_id=session_id,
                    user_message=data.message,
                    assistant_text=assistant_text,
                    metadata={
                        "agentName": agent_name,
                        "teamId": data.team_id,
                        "teamName": data.team_name,
                        "model": model_name,
                        "skillIds": [item.id for item in completed_skill_runs],
                    },
                )
                if memory_facts:
                    yield _sse_event("MEMORY_SUMMARY", {
                        "agentId": agent_id,
                        "agentName": agent_name,
                        "facts": [fact.model_dump(mode="json") for fact in memory_facts],
                    })

        except Exception as e:
            # Agent 执行过程中的异常处理
            assistant_error = f"Agent 执行异常: {str(e)}"
            failed_skill_runs = _set_skill_run_status(skill_runs, "failed")
            _record_run_event(
                session_id=session_id,
                event_type="ERROR",
                persist=persist_run,
                scope="leader",
                agent_id=agent_id,
                agent_name=agent_name,
                status="failed",
                title="Agent execution failed",
                content=assistant_error,
                payload={"model": model_name},
            )
            if persist_run:
                _append_assistant_message(
                    session_id=session_id,
                    agent_id=agent_id,
                    agent_name=agent_name,
                    model_name=model_name,
                    content=assistant_text,
                    error=assistant_error,
                    skill_calls=failed_skill_runs,
                    team_activities=_set_team_activity_status(team_activities, "failed"),
                )
                append_agent_memory_event(
                    agent_id=agent_id,
                    event_type="assistant_message",
                    title="Assistant 异常回复",
                    content=assistant_text or assistant_error,
                    session_id=session_id,
                    metadata={
                        "agentName": agent_name,
                        "model": model_name,
                        "error": assistant_error,
                        "skillIds": [item.id for item in failed_skill_runs],
                    },
                )
            if failed_skill_runs:
                yield _sse_event("SKILL_END", {
                    "skills": [item.model_dump(by_alias=True) for item in failed_skill_runs],
                    "status": "failed",
                })
            if team_activities:
                yield _sse_event("TEAM_ACTIVITY", {
                    "activities": [
                        item.model_dump(by_alias=True)
                        for item in _set_team_activity_status(team_activities, "failed")
                    ],
                })
            yield _sse_event("ERROR", {
                "message": assistant_error,
            })

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/resume")
async def resume_interaction(data: ChatResumeRequest) -> StreamingResponse:
    """Resume a paused Agent run after user confirmation or external result."""

    async def event_stream() -> AsyncGenerator[str, None]:
        interaction = PENDING_INTERACTIONS.get(data.interaction_id)
        agent = PENDING_AGENTS.get(data.interaction_id)
        if interaction is None or agent is None:
            yield _sse_event("ERROR", {"message": "待处理交互不存在或后端已重启。"})
            return

        try:
            resume_event = _build_resume_event(interaction, data)
        except Exception as exc:
            yield _sse_event("ERROR", {"message": f"恢复事件构建失败: {exc}"})
            return

        assistant_text = interaction.assistant_text
        assistant_error: str | None = None
        skill_runs = interaction.skill_runs
        team_activities = interaction.team_activities
        activity = _find_team_activity(team_activities, interaction.agent_id)
        _record_run_event(
            session_id=interaction.session_id,
            event_type="HITL_RESOLVED",
            persist=True,
            scope=interaction.scope,
            agent_id=interaction.agent_id,
            agent_name=interaction.agent_name,
            team_activity_id=activity.id if activity else None,
            status="completed" if data.action == "approve" else "rejected",
            title=f"{interaction.scope.title()} human approval {data.action}",
            payload={
                "interactionId": interaction.id,
                "interactionType": interaction.type,
                "action": data.action,
                "externalResultCount": len(data.external_results),
            },
        )

        try:
            if interaction.scope == "worker":
                chat_data = ChatMessage.model_validate(interaction.chat_data or {})
                worker_member = ChatTeamMemberPayload.model_validate(interaction.worker_member or {})
                worker_text = interaction.worker_text
                worker_thinking = interaction.worker_thinking
                worker_tool_calls = interaction.worker_tool_calls

                activity = _find_team_activity(team_activities, worker_member.agent_id)
                if activity:
                    _replace_team_activity(
                        team_activities,
                        activity.model_copy(update={
                            "status": "running",
                            "detail": "人工确认已处理，继续 Worker ReAct。",
                            "output": worker_text,
                            "thinking": worker_thinking,
                            "tool_calls": worker_tool_calls,
                        }),
                    )
                    yield _sse_event("TEAM_ACTIVITY", {
                        "activities": [item.model_dump(by_alias=True) for item in team_activities],
                    })

                async for event in agent.reply_stream(resume_event):
                    event_data = event.model_dump(mode="json")
                    event_type = event_data.pop("type", "UNKNOWN")
                    _attach_provider_cache_usage(event_type, event_data)
                    if event_type in HITL_EVENT_TYPES:
                        next_interaction = _create_pending_interaction(
                            interaction_type=event_type,
                            agent=agent,
                            session_id=interaction.session_id,
                            agent_id=worker_member.agent_id,
                            agent_name=interaction.agent_name,
                            model_name=interaction.model_name,
                            reply_id=str(event_data.get("reply_id") or ""),
                            tool_calls=list(event_data.get("tool_calls") or []),
                            message=interaction.message,
                            assistant_text="",
                            skill_runs=skill_runs,
                            team_activities=team_activities,
                            scope="worker",
                            chat_data=interaction.chat_data,
                            leader_system_prompt=interaction.leader_system_prompt,
                            worker_member=interaction.worker_member,
                            worker_text=worker_text,
                            worker_thinking=worker_thinking,
                            worker_tool_calls=worker_tool_calls,
                        )
                        activity = _find_team_activity(team_activities, worker_member.agent_id)
                        if activity:
                            _replace_team_activity(
                                team_activities,
                                activity.model_copy(update={
                                    "status": "waiting",
                                    "detail": "等待人工确认后继续 Worker ReAct。",
                                    "output": worker_text,
                                    "thinking": worker_thinking,
                                    "tool_calls": worker_tool_calls,
                                }),
                            )
                            yield _sse_event("TEAM_ACTIVITY", {
                                "activities": [item.model_dump(by_alias=True) for item in team_activities],
                            })
                        _clear_pending_interaction(interaction.id)
                        yield _sse_event("HITL_REQUIRED", _hitl_payload(next_interaction))
                        return

                    if event_type == "TEXT_BLOCK_DELTA":
                        worker_text += str(event_data.get("delta", ""))
                    elif event_type == "THINKING_BLOCK_DELTA":
                        worker_thinking += str(event_data.get("delta", ""))

                    next_tool_calls = _update_worker_tool_calls(
                        worker_tool_calls,
                        event_type,
                        event_data,
                    )
                    if next_tool_calls is not worker_tool_calls:
                        worker_tool_calls = next_tool_calls

                    activity = _find_team_activity(team_activities, worker_member.agent_id)
                    if activity:
                        _replace_team_activity(
                            team_activities,
                            activity.model_copy(update={
                                "status": "running",
                                "output": worker_text,
                                "thinking": worker_thinking,
                                "tool_calls": worker_tool_calls,
                                "detail": _trim_text(worker_text, 120) or activity.detail,
                            }),
                        )
                        yield _sse_event("TEAM_MEMBER_EVENT", {
                            "activityId": activity.id,
                            "agentId": worker_member.agent_id,
                            "agentName": interaction.agent_name,
                            "role": "worker",
                            "eventType": event_type,
                            "event": event_data,
                        })

                activity = _find_team_activity(team_activities, worker_member.agent_id)
                if activity:
                    _replace_team_activity(
                        team_activities,
                        activity.model_copy(update={
                            "status": "completed",
                            "output": worker_text,
                            "thinking": worker_thinking,
                            "tool_calls": worker_tool_calls,
                            "detail": _trim_text(worker_text, 160) or "Worker 已完成本轮 ReAct 循环。",
                        }),
                    )
                    yield _sse_event("TEAM_ACTIVITY", {
                        "activities": [item.model_dump(by_alias=True) for item in team_activities],
                    })

                append_agent_memory_event(
                    agent_id=worker_member.agent_id,
                    event_type="team_message",
                    title=f"Team Worker 输出: {interaction.agent_name}",
                    content=worker_text,
                    session_id=interaction.session_id,
                    metadata={
                        "teamId": chat_data.team_id,
                        "teamName": chat_data.team_name,
                        "leaderAgentId": chat_data.agent_id,
                        "model": interaction.model_name,
                        "resumedFromHitl": True,
                    },
                )
                worker_memory_facts = await maybe_summarize_agent_run(
                    agent_id=worker_member.agent_id,
                    agent_name=interaction.agent_name,
                    session_id=interaction.session_id,
                    user_message=interaction.message,
                    assistant_text=worker_text,
                    metadata={
                        "teamId": chat_data.team_id,
                        "teamName": chat_data.team_name,
                        "leaderAgentId": chat_data.agent_id,
                        "model": interaction.model_name,
                        "resumedFromHitl": True,
                    },
                )
                if worker_memory_facts:
                    yield _sse_event("MEMORY_SUMMARY", {
                        "agentId": worker_member.agent_id,
                        "agentName": interaction.agent_name,
                        "facts": [fact.model_dump(mode="json") for fact in worker_memory_facts],
                    })

                _clear_pending_interaction(interaction.id)
                async for worker_event in _run_team_workers(
                    data=chat_data,
                    session_id=interaction.session_id,
                    team_activities=team_activities,
                    model_name=interaction.model_name,
                    leader_system_prompt=interaction.leader_system_prompt,
                    skill_runs=skill_runs,
                ):
                    yield worker_event
                if any(activity.status == "waiting" for activity in team_activities):
                    return

                worker_outputs = _collect_worker_outputs(team_activities)
                leader_system_prompt = "\n\n".join(
                    part
                    for part in [
                        interaction.leader_system_prompt,
                        _build_worker_outputs_system_prompt(worker_outputs),
                    ]
                    if part
                )
                leader_agent_id = chat_data.agent_id or interaction.agent_id
                leader_agent_name = chat_data.agent_name or "Leader"
                skill_cache_key = ",".join(sorted(item.id for item in skill_runs))
                runtime_agent_id = (
                    f"{leader_agent_id}:{interaction.session_id}:{interaction.model_name}:"
                    f"thinking-{chat_data.deep_thinking}:skills-{skill_cache_key}:permission-default"
                )
                leader_agent = get_or_create_agent(
                    agent_id=runtime_agent_id,
                    name=leader_agent_name,
                    system_prompt=leader_system_prompt,
                    model_name=interaction.model_name,
                    thinking_enable=chat_data.deep_thinking,
                    permission_mode=PermissionMode.DEFAULT,
                )
                leader_activity = _find_team_activity(team_activities, leader_agent_id)
                if leader_activity:
                    _replace_team_activity(
                        team_activities,
                        leader_activity.model_copy(update={
                            "status": "running",
                            "detail": f"Worker 已完成，Leader 正在基于 {len(worker_outputs)} 个 Worker 输出汇总最终回复。",
                            "leader_context_count": len(worker_outputs),
                        }),
                    )
                    yield _sse_event("TEAM_ACTIVITY", {
                        "activities": [item.model_dump(by_alias=True) for item in team_activities],
                    })

                user_msg = UserMsg(
                    name="user",
                    content=_build_user_content(
                        _build_leader_user_message(chat_data.message, worker_outputs),
                        chat_data.images,
                    ),
                )
                assistant_tool_calls: list[ChatToolRun] = []
                async for event in leader_agent.reply_stream(user_msg):
                    event_data = event.model_dump(mode="json")
                    event_type = event_data.pop("type", "UNKNOWN")
                    _attach_provider_cache_usage(event_type, event_data)
                    next_tool_calls = _update_worker_tool_calls(
                        assistant_tool_calls,
                        event_type,
                        event_data,
                    )
                    if next_tool_calls is not assistant_tool_calls:
                        assistant_tool_calls = next_tool_calls
                    if event_type in HITL_EVENT_TYPES:
                        next_interaction = _create_pending_interaction(
                            interaction_type=event_type,
                            agent=leader_agent,
                            session_id=interaction.session_id,
                            agent_id=leader_agent_id,
                            agent_name=leader_agent_name,
                            model_name=interaction.model_name,
                            reply_id=str(event_data.get("reply_id") or ""),
                            tool_calls=list(event_data.get("tool_calls") or []),
                            message=chat_data.message,
                            assistant_text=assistant_text,
                            skill_runs=skill_runs,
                            team_activities=team_activities,
                            chat_data=interaction.chat_data,
                            leader_system_prompt=leader_system_prompt,
                        )
                        yield _sse_event("HITL_REQUIRED", _hitl_payload(next_interaction))
                        return
                    if event_type == "TEXT_BLOCK_DELTA":
                        assistant_text += str(event_data.get("delta", ""))
                    if event_type == "REPLY_END" and skill_runs:
                        completed_skill_runs = _set_skill_run_status(skill_runs, "completed")
                        yield _sse_event("SKILL_END", {
                            "skills": [item.model_dump(by_alias=True) for item in completed_skill_runs],
                            "status": "completed",
                        })
                    yield _sse_event(event_type, event_data)

                completed_skill_runs = _set_skill_run_status(skill_runs, "completed")
                completed_team_activities = _finalize_team_activities(
                    team_activities,
                    leader_agent_id=leader_agent_id,
                    leader_output=assistant_text,
                )
                _append_assistant_message(
                    session_id=interaction.session_id,
                    agent_id=leader_agent_id,
                    agent_name=leader_agent_name,
                    model_name=interaction.model_name,
                    content=assistant_text,
                    error=assistant_error,
                    skill_calls=completed_skill_runs,
                    tool_calls=assistant_tool_calls,
                    team_activities=completed_team_activities,
                )
                yield _sse_event("TEAM_ACTIVITY", {
                    "activities": [item.model_dump(by_alias=True) for item in completed_team_activities],
                })
                append_agent_memory_event(
                    agent_id=leader_agent_id,
                    event_type="assistant_message",
                    title="Team Leader 回复",
                    content=assistant_text,
                    session_id=interaction.session_id,
                    metadata={
                        "agentName": leader_agent_name,
                        "teamId": chat_data.team_id,
                        "teamName": chat_data.team_name,
                        "model": interaction.model_name,
                        "skillIds": [item.id for item in completed_skill_runs],
                        "resumedFromWorkerHitl": True,
                    },
                )
                memory_facts = await maybe_summarize_agent_run(
                    agent_id=leader_agent_id,
                    agent_name=leader_agent_name,
                    session_id=interaction.session_id,
                    user_message=chat_data.message,
                    assistant_text=assistant_text,
                    metadata={
                        "agentName": leader_agent_name,
                        "teamId": chat_data.team_id,
                        "teamName": chat_data.team_name,
                        "model": interaction.model_name,
                        "skillIds": [item.id for item in completed_skill_runs],
                        "resumedFromWorkerHitl": True,
                    },
                )
                if memory_facts:
                    yield _sse_event("MEMORY_SUMMARY", {
                        "agentId": leader_agent_id,
                        "agentName": leader_agent_name,
                        "facts": [fact.model_dump(mode="json") for fact in memory_facts],
                    })
                return

            assistant_tool_calls: list[ChatToolRun] = []
            async for event in agent.reply_stream(resume_event):
                event_data = event.model_dump(mode="json")
                event_type = event_data.pop("type", "UNKNOWN")
                _attach_provider_cache_usage(event_type, event_data)
                next_tool_calls = _update_worker_tool_calls(
                    assistant_tool_calls,
                    event_type,
                    event_data,
                )
                if next_tool_calls is not assistant_tool_calls:
                    assistant_tool_calls = next_tool_calls
                if event_type in HITL_EVENT_TYPES:
                    next_interaction = _create_pending_interaction(
                        interaction_type=event_type,
                        agent=agent,
                        session_id=interaction.session_id,
                        agent_id=interaction.agent_id,
                        agent_name=interaction.agent_name,
                        model_name=interaction.model_name,
                        reply_id=str(event_data.get("reply_id") or ""),
                        tool_calls=list(event_data.get("tool_calls") or []),
                        message=interaction.message,
                        assistant_text=assistant_text,
                        skill_runs=skill_runs,
                        team_activities=team_activities,
                    )
                    _clear_pending_interaction(interaction.id)
                    yield _sse_event("HITL_REQUIRED", _hitl_payload(next_interaction))
                    return

                if event_type == "TEXT_BLOCK_DELTA":
                    assistant_text += str(event_data.get("delta", ""))

                if event_type == "REPLY_END" and skill_runs:
                    completed_skill_runs = _set_skill_run_status(skill_runs, "completed")
                    yield _sse_event("SKILL_END", {
                        "skills": [item.model_dump(by_alias=True) for item in completed_skill_runs],
                        "status": "completed",
                    })

                yield _sse_event(event_type, event_data)

            completed_skill_runs = _set_skill_run_status(skill_runs, "completed")
            completed_team_activities = _finalize_team_activities(
                team_activities,
                leader_agent_id=interaction.agent_id,
                leader_output=assistant_text,
            )
            _append_assistant_message(
                session_id=interaction.session_id,
                agent_id=interaction.agent_id,
                agent_name=interaction.agent_name,
                model_name=interaction.model_name,
                content=assistant_text,
                error=assistant_error,
                skill_calls=completed_skill_runs,
                tool_calls=assistant_tool_calls,
                team_activities=completed_team_activities,
            )
            if team_activities:
                yield _sse_event("TEAM_ACTIVITY", {
                    "activities": [item.model_dump(by_alias=True) for item in completed_team_activities],
                })
            append_agent_memory_event(
                agent_id=interaction.agent_id,
                event_type="assistant_message",
                title="Assistant 回复",
                content=assistant_text,
                session_id=interaction.session_id,
                metadata={
                    "agentName": interaction.agent_name,
                    "model": interaction.model_name,
                    "skillIds": [item.id for item in completed_skill_runs],
                    "resumedFromHitl": True,
                },
            )
            memory_facts = await maybe_summarize_agent_run(
                agent_id=interaction.agent_id,
                agent_name=interaction.agent_name,
                session_id=interaction.session_id,
                user_message=interaction.message,
                assistant_text=assistant_text,
                metadata={
                    "agentName": interaction.agent_name,
                    "model": interaction.model_name,
                    "skillIds": [item.id for item in completed_skill_runs],
                    "resumedFromHitl": True,
                },
            )
            if memory_facts:
                yield _sse_event("MEMORY_SUMMARY", {
                    "agentId": interaction.agent_id,
                    "agentName": interaction.agent_name,
                    "facts": [fact.model_dump(mode="json") for fact in memory_facts],
                })
            _clear_pending_interaction(interaction.id)
        except Exception as exc:
            assistant_error = f"Agent 恢复执行异常: {exc}"
            _append_assistant_message(
                session_id=interaction.session_id,
                agent_id=interaction.agent_id,
                agent_name=interaction.agent_name,
                model_name=interaction.model_name,
                content=assistant_text,
                error=assistant_error,
                skill_calls=_set_skill_run_status(skill_runs, "failed"),
                team_activities=_set_team_activity_status(team_activities, "failed"),
            )
            _clear_pending_interaction(interaction.id)
            yield _sse_event("ERROR", {"message": assistant_error})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/sessions")
async def list_sessions() -> list[ChatSession]:
    """
    获取会话列表

    Returns:
        list[ChatSession]: 会话列表
    """
    store = _load_store()
    return sorted(store.sessions, key=lambda item: item.updated_at, reverse=True)


@router.get("/sessions/{session_id}/messages")
async def list_session_messages(session_id: str) -> list[StoredChatMessage]:
    """获取指定会话的消息列表"""
    store = _load_store()
    if session_id not in store.messages:
        raise HTTPException(status_code=404, detail="Session not found")
    return store.messages[session_id]


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(session_id: str) -> None:
    """删除指定会话及消息"""
    store = _load_store()
    if session_id not in store.messages:
        raise HTTPException(status_code=404, detail="Session not found")

    store.sessions = [session for session in store.sessions if session.id != session_id]
    store.messages.pop(session_id, None)
    _save_store(store)


def _sse_event(event_type: str, data: dict) -> str:
    """
    格式化 SSE 事件

    遵循 Server-Sent Events 规范：
      event: <type>
      data: <json>

    Args:
        event_type: 事件类型名称
        data: 事件数据（将被 JSON 序列化）

    Returns:
        str: 符合 SSE 规范的事件字符串
    """
    json_str = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event_type}\ndata: {json_str}\n\n"


def _record_run_event(
    *,
    session_id: str,
    event_type: str,
    persist: bool,
    scope: str = "leader",
    agent_id: str | None = None,
    agent_name: str | None = None,
    team_activity_id: str | None = None,
    status: str = "",
    title: str = "",
    content: str = "",
    payload: dict | None = None,
) -> None:
    """Persist trace events without allowing observability to break execution."""
    if not persist:
        return
    try:
        append_run_event(
            session_id=session_id,
            event_type=event_type,
            scope=scope,
            agent_id=agent_id,
            agent_name=agent_name,
            team_activity_id=team_activity_id,
            status=status,
            title=title,
            content=content,
            payload=payload or {},
        )
    except Exception as exc:
        print(f"[Trace] Failed to record run event {event_type}: {exc}")


def _make_fallback_trace_recorder(
    *,
    session_id: str,
    persist: bool,
    scope: str,
    agent_id: str,
    agent_name: str,
    team_activity_id: str | None = None,
):
    """Create a callback that records application-level fallback attempts."""

    def record(event: dict) -> None:
        event_kind = str(event.get("type") or "attempt")
        model = str(event.get("model") or "")
        status = {
            "attempt": "running",
            "success": "completed",
            "failure": "failed",
        }.get(event_kind, "running")
        title = {
            "attempt": f"Trying model: {model}",
            "success": f"Model succeeded: {model}",
            "failure": f"Model failed: {model}",
        }.get(event_kind, f"Model fallback event: {model}")
        _record_run_event(
            session_id=session_id,
            event_type=f"MODEL_FALLBACK_{event_kind.upper()}",
            persist=persist,
            scope=scope,
            agent_id=agent_id,
            agent_name=agent_name,
            team_activity_id=team_activity_id,
            status=status,
            title=title,
            content=str(event.get("error") or ""),
            payload=event,
        )

    return record


def _workspace_trace_payload(guardrails: AgentGuardrails) -> dict:
    """Build trace payload for local/Docker execution workspace config."""
    return {
        "backend": guardrails.execution_backend,
        "filesystemScope": guardrails.filesystem_scope,
        "networkEnabled": guardrails.network_enabled,
        "toolPermissionProfile": guardrails.tool_permission_profile,
        "timeoutSeconds": guardrails.timeout_seconds,
        "dockerImage": guardrails.docker_image if guardrails.execution_backend == "docker" else "",
        "persistentSandbox": guardrails.persistent_sandbox if guardrails.execution_backend == "docker" else False,
    }


def _record_workspace_event(
    *,
    session_id: str,
    persist: bool,
    scope: str,
    agent_id: str,
    agent_name: str,
    guardrails: AgentGuardrails,
    team_activity_id: str | None = None,
) -> None:
    payload = _workspace_trace_payload(guardrails)
    title = (
        f"Docker workspace: {guardrails.docker_image}"
        if guardrails.execution_backend == "docker"
        else "Local workspace"
    )
    _record_run_event(
        session_id=session_id,
        event_type="WORKSPACE_CONFIG",
        persist=persist,
        scope=scope,
        agent_id=agent_id,
        agent_name=agent_name,
        team_activity_id=team_activity_id,
        status="configured",
        title=title,
        content=(
            f"{payload['backend']} · scope={payload['filesystemScope']} · "
            f"profile={payload['toolPermissionProfile']}"
        ),
        payload=payload,
    )


def _record_agent_scope_event(
    *,
    session_id: str,
    event_type: str,
    event_data: dict,
    persist: bool,
    scope: str,
    agent_id: str,
    agent_name: str,
    team_activity_id: str | None = None,
) -> None:
    """Record selected AgentScope events as compact trace events."""
    if event_type not in TRACE_EVENT_TYPES:
        return
    tool_name = str(event_data.get("tool_call_name") or "")
    model_name = str(event_data.get("model_name") or "")
    title = {
        "REPLY_START": "Reply started",
        "REPLY_END": "Reply completed",
        "MODEL_CALL_START": f"Model call started{f': {model_name}' if model_name else ''}",
        "MODEL_CALL_END": f"Model call completed{f': {model_name}' if model_name else ''}",
        "TOOL_CALL_START": f"Tool call started{f': {tool_name}' if tool_name else ''}",
        "TOOL_CALL_END": f"Tool call ended{f': {tool_name}' if tool_name else ''}",
        "TOOL_RESULT_START": f"Tool result started{f': {tool_name}' if tool_name else ''}",
        "TOOL_RESULT_END": f"Tool result completed{f': {tool_name}' if tool_name else ''}",
    }.get(event_type, event_type)
    status = (
        "completed"
        if event_type in {"REPLY_END", "MODEL_CALL_END", "TOOL_RESULT_END"}
        else "running"
    )
    payload = dict(event_data)
    if "delta" in payload:
        payload["delta"] = _trim_text(str(payload.get("delta") or ""), 240)
    _record_run_event(
        session_id=session_id,
        event_type=event_type,
        persist=persist,
        scope=scope,
        agent_id=agent_id,
        agent_name=agent_name,
        team_activity_id=team_activity_id,
        status=status,
        title=title,
        payload=payload,
    )
    provider_cache = payload.get("provider_cache")
    if event_type == "MODEL_CALL_END" and isinstance(provider_cache, dict) and provider_cache.get("available"):
        created_tokens = int(provider_cache.get("created_tokens") or 0)
        hit_tokens = int(provider_cache.get("hit_tokens") or 0)
        _record_run_event(
            session_id=session_id,
            event_type="MODEL_CACHE_USAGE",
            persist=persist,
            scope=scope,
            agent_id=agent_id,
            agent_name=agent_name,
            team_activity_id=team_activity_id,
            status="completed",
            title="Provider prompt cache usage",
            content=f"created={created_tokens}, hit={hit_tokens}",
            payload={
                "model": model_name,
                "createdTokens": created_tokens,
                "hitTokens": hit_tokens,
                "estimatedSavedInputTokens": hit_tokens,
            },
        )


def _attach_provider_cache_usage(event_type: str, event_data: dict) -> None:
    """Expose provider-side prompt cache usage on MODEL_CALL_END events."""

    if event_type != "MODEL_CALL_END":
        return

    metadata = event_data.get("metadata")
    if not isinstance(metadata, dict):
        return

    cache_creation = int(metadata.get("cache_creation_input_tokens") or 0)
    cache_read = int(metadata.get("cache_input_tokens") or 0)
    event_data["cache_creation_input_tokens"] = cache_creation
    event_data["cache_input_tokens"] = cache_read
    event_data["provider_cache"] = {
        "created_tokens": cache_creation,
        "hit_tokens": cache_read,
        "available": bool(cache_creation or cache_read),
    }


def _create_pending_interaction(
    *,
    interaction_type: str,
    agent: object,
    session_id: str,
    agent_id: str,
    agent_name: str,
    model_name: str,
    reply_id: str,
    tool_calls: list[dict],
    message: str,
    assistant_text: str,
    skill_runs: list[ChatSkillRun],
    team_activities: list[ChatTeamActivity],
    scope: str = "leader",
    chat_data: dict | None = None,
    leader_system_prompt: str = "",
    worker_member: dict | None = None,
    worker_text: str = "",
    worker_thinking: str = "",
    worker_tool_calls: list[ChatToolRun] | None = None,
    persist: bool = True,
) -> PendingInteraction:
    """Create and cache an in-process human interaction."""
    interaction = PendingInteraction(
        id=uuid.uuid4().hex,
        type=interaction_type,
        session_id=session_id,
        agent_id=agent_id,
        agent_name=agent_name,
        model_name=model_name,
        reply_id=reply_id,
        tool_calls=tool_calls,
        message=message,
        assistant_text=assistant_text,
        skill_runs=skill_runs,
        team_activities=team_activities,
        scope=scope,
        chat_data=chat_data,
        leader_system_prompt=leader_system_prompt,
        worker_member=worker_member,
        worker_text=worker_text,
        worker_thinking=worker_thinking,
        worker_tool_calls=worker_tool_calls or [],
    )
    PENDING_INTERACTIONS[interaction.id] = interaction
    PENDING_AGENTS[interaction.id] = agent
    activity = _find_team_activity(interaction.team_activities, interaction.agent_id)
    _record_run_event(
        session_id=interaction.session_id,
        event_type="HITL_REQUIRED",
        persist=persist,
        scope=interaction.scope,
        agent_id=interaction.agent_id,
        agent_name=interaction.agent_name,
        team_activity_id=activity.id if activity else None,
        status="waiting",
        title=f"{interaction.scope.title()} human approval required",
        payload=_hitl_payload(interaction),
    )
    return interaction


def _clear_pending_interaction(interaction_id: str) -> None:
    """Remove a pending interaction and its runtime Agent reference."""
    PENDING_INTERACTIONS.pop(interaction_id, None)
    PENDING_AGENTS.pop(interaction_id, None)


def _hitl_payload(interaction: PendingInteraction) -> dict:
    """Build frontend payload for a pending human interaction."""
    return {
        "interactionId": interaction.id,
        "type": interaction.type,
        "sessionId": interaction.session_id,
        "agentId": interaction.agent_id,
        "agentName": interaction.agent_name,
        "replyId": interaction.reply_id,
        "toolCalls": interaction.tool_calls,
        "scope": interaction.scope,
        "teamActivityId": _find_team_activity(interaction.team_activities, interaction.agent_id).id
        if interaction.scope == "worker" and _find_team_activity(interaction.team_activities, interaction.agent_id)
        else None,
        "createdAt": interaction.created_at,
    }


def _build_resume_event(
    interaction: PendingInteraction,
    data: ChatResumeRequest,
) -> UserConfirmResultEvent | ExternalExecutionResultEvent:
    """Build AgentScope resume event from the user's decision."""
    if interaction.type == "REQUIRE_USER_CONFIRM":
        confirmed = data.action == "approve"
        return UserConfirmResultEvent(
            reply_id=interaction.reply_id,
            confirm_results=[
                ConfirmResult(
                    confirmed=confirmed,
                    tool_call=ToolCallBlock.model_validate(tool_call),
                    rules=None,
                )
                for tool_call in interaction.tool_calls
            ],
        )

    if interaction.type == "REQUIRE_EXTERNAL_EXECUTION":
        return ExternalExecutionResultEvent(
            reply_id=interaction.reply_id,
            execution_results=[
                ToolResultBlock(
                    id=str(tool_call.get("id", "")),
                    name=str(tool_call.get("name", "tool")),
                    output=data.external_results.get(str(tool_call.get("id", "")), ""),
                    state=ToolResultState.SUCCESS,
                )
                for tool_call in interaction.tool_calls
            ],
        )

    raise ValueError(f"Unsupported interaction type: {interaction.type}")


def _load_store() -> ChatStore:
    """读取对话存储文件"""
    migrate_json_object_file(CHAT_NAMESPACE, CHAT_STORE_KEY, CHAT_DATA_PATH)
    data = load_chat_store()
    if not data.get("sessions") and not data.get("messages"):
        legacy = get_json_record(CHAT_NAMESPACE, CHAT_STORE_KEY)
        if legacy:
            save_chat_store(legacy)
            data = load_chat_store()
    if not data:
        return ChatStore()

    try:
        return ChatStore.model_validate(data)
    except Exception:
        return ChatStore()


def _save_store(store: ChatStore) -> None:
    """写入对话存储文件"""
    save_chat_store(store.model_dump(mode="json", by_alias=True))


def _append_user_message(
    *,
    session_id: str,
    agent_id: str,
    agent_name: str,
    team_id: str | None,
    team_name: str | None,
    model_name: str,
    content: str,
    images: list[ChatImagePayload],
) -> None:
    """追加用户消息，并创建或更新会话摘要"""
    now = datetime.now(timezone.utc).isoformat()
    store = _load_store()
    messages = store.messages.setdefault(session_id, [])
    messages.append(StoredChatMessage(
        id=uuid.uuid4().hex,
        role="user",
        content=content,
        agent_id=agent_id,
        agent_name=agent_name,
        model_name=model_name,
        images=images,
        created_at=now,
    ))

    title = _make_session_title(content)
    existing = next((session for session in store.sessions if session.id == session_id), None)
    if existing:
        existing.agent_id = agent_id
        existing.agent_name = agent_name
        existing.team_id = team_id
        existing.team_name = team_name
        existing.updated_at = now
        existing.message_count = len(messages)
    else:
        store.sessions.append(ChatSession(
            id=session_id,
            title=title,
            agent_id=agent_id,
            agent_name=agent_name,
            team_id=team_id,
            team_name=team_name,
            created_at=now,
            updated_at=now,
            message_count=len(messages),
        ))

    _save_store(store)


def _append_assistant_message(
    *,
    session_id: str,
    agent_id: str,
    agent_name: str,
    model_name: str,
    content: str,
    error: str | None,
    skill_calls: list[ChatSkillRun] | None = None,
    tool_calls: list[ChatToolRun] | None = None,
    team_activities: list[ChatTeamActivity] | None = None,
) -> None:
    """追加助手消息，并更新会话摘要"""
    now = datetime.now(timezone.utc).isoformat()
    store = _load_store()
    messages = store.messages.setdefault(session_id, [])
    messages.append(StoredChatMessage(
        id=uuid.uuid4().hex,
        role="assistant",
        content=content,
        agent_id=agent_id,
        agent_name=agent_name,
        model_name=model_name,
        error=error,
        tool_calls=tool_calls or [],
        skill_calls=skill_calls or [],
        team_activities=team_activities or [],
        created_at=now,
    ))

    existing = next((session for session in store.sessions if session.id == session_id), None)
    if existing:
        existing.updated_at = now
        existing.message_count = len(messages)

    _save_store(store)


def _make_session_title(content: str) -> str:
    """用首条用户消息生成会话标题"""
    title = " ".join(content.strip().split())
    if not title:
        return "新对话"
    return title[:28] + ("..." if len(title) > 28 else "")


def _build_user_content(
    text: str,
    images: list[ChatImagePayload],
) -> str | list[TextBlock | DataBlock]:
    """构建 AgentScope 用户消息内容"""
    if not images:
        return text

    blocks: list[TextBlock | DataBlock] = [TextBlock(text=text)]
    for image in images:
        blocks.append(DataBlock(
            name=image.name or None,
            source=Base64Source(
                data=image.data,
                media_type=image.media_type,
            ),
        ))
    return blocks


def _build_runtime_guardrails(agent_config: object | None) -> AgentGuardrails:
    guardrails = getattr(agent_config, "guardrails", None)
    if not guardrails:
        return AgentGuardrails()
    return AgentGuardrails(
        filesystem_scope=getattr(guardrails, "filesystem_scope", "/") or "/",
        shell_blacklist=list(getattr(guardrails, "shell_blacklist", []) or []),
        network_enabled=bool(getattr(guardrails, "network_enabled", True)),
        human_approval=bool(getattr(guardrails, "human_approval", False)),
        tool_permission_mode=str(getattr(guardrails, "tool_permission_mode", "accept_edits") or "accept_edits"),
        tool_permission_profile=str(
            getattr(guardrails, "tool_permission_profile", "")
            or _profile_from_legacy_tool_mode(getattr(guardrails, "tool_permission_mode", "accept_edits"))
        ),
        execution_backend=str(getattr(guardrails, "execution_backend", "local") or "local"),
        docker_image=str(getattr(guardrails, "docker_image", "python:3.11-slim") or "python:3.11-slim"),
        persistent_sandbox=bool(getattr(guardrails, "persistent_sandbox", False)),
        timeout_seconds=int(getattr(guardrails, "timeout_seconds", 300) or 300),
    )


def _profile_from_legacy_tool_mode(mode: object) -> str:
    value = str(mode or "accept_edits").strip().lower()
    if value == "ask_every_time":
        return "ask_every_time"
    if value == "bypass":
        return "trusted_tools"
    return "confirm_destructive"


def _permission_mode_for_guardrails(guardrails: AgentGuardrails) -> PermissionMode:
    if guardrails.human_approval:
        return PermissionMode.DEFAULT

    profile = (guardrails.tool_permission_profile or "").strip().lower()
    if profile == "ask_every_time":
        return PermissionMode.DEFAULT
    if profile in {"trusted_tools", "read_only"}:
        return PermissionMode.BYPASS
    if profile == "confirm_destructive":
        return PermissionMode.ACCEPT_EDITS

    mode = (guardrails.tool_permission_mode or "accept_edits").strip().lower()
    if mode == "ask_every_time":
        return PermissionMode.DEFAULT
    if mode == "bypass":
        return PermissionMode.BYPASS
    return PermissionMode.ACCEPT_EDITS


def _build_runtime_mcp_servers(agent_config: object | None) -> list[McpRuntimeSpec]:
    tool_ids = list(getattr(agent_config, "mcp_tools", []) or [])
    specs: list[McpRuntimeSpec] = []
    for item in resolve_mcp_runtime_servers(tool_ids):
        specs.append(McpRuntimeSpec(
            name=str(item.get("name") or ""),
            transport=str(item.get("transport") or ""),
            url=str(item.get("url") or ""),
            command=str(item.get("command") or ""),
            headers=dict(item.get("headers") or {}),
            enable_tools=list(item.get("enable_tools") or []),
        ))
    return specs


def _build_runtime_knowledge_tools(agent_config: object | None) -> list[KnowledgeToolRuntimeSpec]:
    tool_ids = list(getattr(agent_config, "knowledge_tools", []) or [])
    specs: list[KnowledgeToolRuntimeSpec] = []
    for item in get_enabled_knowledge_tools(tool_ids):
        specs.append(KnowledgeToolRuntimeSpec(
            id=str(item.get("id") or ""),
            name=str(item.get("name") or "KnowledgeBase"),
            description=str(item.get("description") or ""),
            url=str(item.get("url") or ""),
            authorization=str(item.get("authorization") or ""),
            headers=dict(item.get("headers") or {}),
            default_payload=dict(item.get("defaultPayload") or item.get("default_payload") or {}),
            parameter_instructions=str(item.get("parameterInstructions") or item.get("parameter_instructions") or ""),
        ))
    return specs


def _build_conversation_history_prompt(session_id: str | None, limit: int = 12) -> str:
    if not session_id:
        return ""

    store = _load_store()
    messages = store.messages.get(session_id, [])
    if not messages:
        return ""

    recent = messages[-limit:]
    lines = [
        "## Conversation History",
        "The following are recent messages from this session. Use them for continuity, but prioritize the latest user request.",
    ]
    for message in recent:
        content = " ".join((message.content or "").split())
        if not content:
            continue
        if len(content) > 1200:
            content = content[:1200] + "..."
        role = "User" if message.role == "user" else "Assistant"
        lines.append(f"{role}: {content}")

    return "\n".join(lines) if len(lines) > 2 else ""


async def _run_team_workers(
    *,
    data: ChatMessage,
    session_id: str,
    team_activities: list[ChatTeamActivity],
    model_name: str,
    leader_system_prompt: str,
    skill_runs: list[ChatSkillRun],
) -> AsyncGenerator[str, None]:
    """Run worker Agents concurrently and merge their SSE events."""
    workers = [
        member
        for member in data.team_members
        if member.role != "leader"
        and member.agent_id != data.agent_id
        and (
            not (activity := _find_team_activity(team_activities, member.agent_id))
            or activity.status not in {"completed", "waiting"}
        )
    ]
    if not workers:
        return

    queue: asyncio.Queue[str | None] = asyncio.Queue()
    pause_event = asyncio.Event()

    for member in workers:
        activity = _find_team_activity(team_activities, member.agent_id)
        if activity:
            waiting_for = [
                dep
                for dep in _member_dependency_ids(member, workers)
                if dep in {worker.agent_id for worker in workers}
            ]
            _replace_team_activity(
                team_activities,
                        activity.model_copy(update={
                            "status": "pending" if waiting_for else "running",
                            "execution_mode": member.execution_mode,
                            "wait_reason": (
                                f"等待依赖 Worker 完成：{', '.join(_member_names(waiting_for, workers))}"
                                if waiting_for
                                else ""
                            ),
                            "detail": (
                                f"等待依赖 Worker 完成：{', '.join(_member_names(waiting_for, workers))}"
                                if waiting_for
                        else activity.detail
                    ),
                }),
            )

    yield _sse_event("TEAM_ACTIVITY", {
        "activities": [item.model_dump(by_alias=True) for item in team_activities],
    })

    async def run_worker(
        member: ChatTeamMemberPayload,
        dependency_outputs: list[dict[str, str]],
    ) -> None:
        activity = _find_team_activity(team_activities, member.agent_id)
        if activity is None:
            return
        worker_config = get_registered_agent(member.agent_id)
        runtime_model_settings = get_effective_model_settings()
        worker_name = worker_config.name if worker_config else member.name
        worker_prompt = worker_config.system_prompt if worker_config else member.description
        worker_skill_ids = worker_config.skills if worker_config else []
        worker_plan_prompt = _build_agent_plan_prompt(worker_config)
        worker_model_config = worker_config.model_config_data if worker_config else None
        worker_model_options = AgentModelOptions(
            temperature=worker_model_config.temperature if worker_model_config else None,
            top_p=worker_model_config.top_p if worker_model_config else None,
            max_tokens=worker_model_config.max_tokens if worker_model_config else None,
        )
        worker_guardrails = _build_runtime_guardrails(worker_config)
        worker_mcp_servers = _build_runtime_mcp_servers(worker_config)
        worker_knowledge_tools = _build_runtime_knowledge_tools(worker_config)
        worker_skills = select_relevant_skills(worker_skill_ids, data.message)
        worker_skill_prompt = build_skill_system_prompt_from_skills(worker_skills)
        worker_memory_prompt = _build_agent_memory_prompt(member.agent_id)
        worker_runtime_context_prompt = _build_runtime_context_prompt()
        worker_system_prompt = "\n\n".join(
            part
            for part in [
                worker_prompt or "You are a helpful worker agent.",
                worker_runtime_context_prompt,
                worker_plan_prompt,
                worker_memory_prompt,
                _build_worker_system_prompt(data.team_name, data.agent_name, member),
                worker_skill_prompt,
            ]
            if part
        )
        worker_cache_key = ",".join(sorted(skill.id for skill in worker_skills))
        worker_fallback_names = worker_model_config.fallback if worker_model_config else runtime_model_settings.fallback_models
        worker_fallback_cache_key = ",".join(worker_fallback_names)
        worker_mcp_cache_key = ",".join(
            f"{server.name}:{','.join(server.enable_tools)}"
            for server in worker_mcp_servers
        )
        worker_knowledge_cache_key = ",".join(tool.id for tool in worker_knowledge_tools)
        runtime_worker_id = (
            f"{member.agent_id}:{session_id}:{model_name}:team-worker:"
            f"thinking-{data.deep_thinking}:fallbacks-{worker_fallback_cache_key}:"
            f"skills-{worker_cache_key}:mcp-{worker_mcp_cache_key}:"
            f"knowledge-{worker_knowledge_cache_key}:"
            f"hitl-{worker_guardrails.human_approval}:tool-permission-{worker_guardrails.tool_permission_mode}"
            f":tool-profile-{worker_guardrails.tool_permission_profile}"
            f":exec-{worker_guardrails.execution_backend}:docker-{worker_guardrails.docker_image}"
            f":persist-sandbox-{worker_guardrails.persistent_sandbox}"
        )
        worker_agent = get_or_create_agent(
            agent_id=runtime_worker_id,
            name=worker_name,
            system_prompt=worker_system_prompt,
            model_name=model_name,
            fallback_names=worker_fallback_names,
            thinking_enable=data.deep_thinking,
            permission_mode=_permission_mode_for_guardrails(worker_guardrails),
            model_options=worker_model_options,
            guardrails=worker_guardrails,
            mcp_servers=worker_mcp_servers,
            knowledge_tools=worker_knowledge_tools,
            on_fallback_event=_make_fallback_trace_recorder(
                session_id=session_id,
                persist=not data.sandbox,
                scope="worker",
                agent_id=member.agent_id,
                agent_name=worker_name,
                team_activity_id=activity.id if activity else None,
            ),
        )
        _record_workspace_event(
            session_id=session_id,
            persist=not data.sandbox,
            scope="worker",
            agent_id=member.agent_id,
            agent_name=worker_name,
            team_activity_id=activity.id if activity else None,
            guardrails=worker_guardrails,
        )
        worker_text = ""
        worker_thinking = ""
        worker_tool_calls: list[ChatToolRun] = []

        try:
            worker_msg = UserMsg(
                name="team_leader",
                content=_build_user_content(
                    _build_worker_user_message(data.message, member, dependency_outputs),
                    data.images,
                ),
            )
            async for event in worker_agent.reply_stream(worker_msg):
                event_data = event.model_dump(mode="json")
                event_type = event_data.pop("type", "UNKNOWN")
                _attach_provider_cache_usage(event_type, event_data)
                activity = _find_team_activity(team_activities, member.agent_id)
                _record_agent_scope_event(
                    session_id=session_id,
                    event_type=event_type,
                    event_data=event_data,
                    persist=not data.sandbox,
                    scope="worker",
                    agent_id=member.agent_id,
                    agent_name=worker_name,
                    team_activity_id=activity.id if activity else None,
                )
                if event_type in HITL_EVENT_TYPES:
                    if activity:
                        _replace_team_activity(
                            team_activities,
                            activity.model_copy(update={
                                "status": "waiting",
                                "detail": "等待人工确认后继续 Worker ReAct。",
                                "output": worker_text,
                                "thinking": worker_thinking,
                                "tool_calls": worker_tool_calls,
                            }),
                        )
                    interaction = _create_pending_interaction(
                        interaction_type=event_type,
                        agent=worker_agent,
                        session_id=session_id,
                        agent_id=member.agent_id,
                        agent_name=worker_name,
                        model_name=model_name,
                        reply_id=str(event_data.get("reply_id") or ""),
                        tool_calls=list(event_data.get("tool_calls") or []),
                        message=_build_worker_user_message(data.message, member, dependency_outputs),
                        assistant_text="",
                        skill_runs=skill_runs,
                        team_activities=team_activities,
                        scope="worker",
                        chat_data=data.model_dump(mode="json"),
                        leader_system_prompt=leader_system_prompt,
                        worker_member=member.model_dump(by_alias=True),
                        worker_text=worker_text,
                        worker_thinking=worker_thinking,
                        worker_tool_calls=worker_tool_calls,
                        persist=not data.sandbox,
                    )
                    await queue.put(_sse_event("TEAM_ACTIVITY", {
                        "activities": [item.model_dump(by_alias=True) for item in team_activities],
                    }))
                    await queue.put(_sse_event("HITL_REQUIRED", _hitl_payload(interaction)))
                    pause_event.set()
                    return
                if event_type == "TEXT_BLOCK_DELTA":
                    worker_text += str(event_data.get("delta", ""))
                    activity = _find_team_activity(team_activities, member.agent_id)
                    if activity:
                        _replace_team_activity(
                            team_activities,
                            activity.model_copy(update={
                                "output": worker_text,
                                "detail": _trim_text(worker_text, 120) or activity.detail,
                            }),
                        )
                elif event_type == "THINKING_BLOCK_DELTA":
                    worker_thinking += str(event_data.get("delta", ""))
                    activity = _find_team_activity(team_activities, member.agent_id)
                    if activity:
                        _replace_team_activity(
                            team_activities,
                            activity.model_copy(update={"thinking": worker_thinking}),
                        )

                next_tool_calls = _update_worker_tool_calls(
                    worker_tool_calls,
                    event_type,
                    event_data,
                )
                if next_tool_calls is not worker_tool_calls:
                    worker_tool_calls = next_tool_calls
                    activity = _find_team_activity(team_activities, member.agent_id)
                    if activity:
                        _replace_team_activity(
                            team_activities,
                            activity.model_copy(update={"tool_calls": worker_tool_calls}),
                        )

                activity = _find_team_activity(team_activities, member.agent_id)
                if activity:
                    await queue.put(_sse_event("TEAM_MEMBER_EVENT", {
                        "activityId": activity.id,
                        "agentId": member.agent_id,
                        "agentName": worker_name,
                        "role": "worker",
                        "eventType": event_type,
                        "event": event_data,
                    }))

            activity = _find_team_activity(team_activities, member.agent_id)
            if activity:
                completed = activity.model_copy(update={
                    "status": "completed",
                    "output": worker_text,
                    "thinking": worker_thinking,
                    "tool_calls": worker_tool_calls,
                    "detail": _trim_text(worker_text, 160) or "Worker 已完成本轮 ReAct 循环。",
                })
                _replace_team_activity(team_activities, completed)
                _record_run_event(
                    session_id=session_id,
                    event_type="WORKER_COMPLETED",
                    persist=not data.sandbox,
                    scope="worker",
                    agent_id=member.agent_id,
                    agent_name=worker_name,
                    team_activity_id=activity.id,
                    status="completed",
                    title=f"Worker completed: {worker_name}",
                    content=_trim_text(worker_text, 1200),
                    payload={
                        "toolCallCount": len(worker_tool_calls),
                        "dependencyCount": len(dependency_outputs),
                    },
                )
            if not data.sandbox:
                append_agent_memory_event(
                    agent_id=member.agent_id,
                    event_type="team_message",
                    title=f"Team Worker 输出: {worker_name}",
                    content=worker_text,
                    session_id=session_id,
                    metadata={
                        "teamId": data.team_id,
                        "teamName": data.team_name,
                        "leaderAgentId": data.agent_id,
                        "model": model_name,
                    },
                )
                worker_memory_facts = await maybe_summarize_agent_run(
                    agent_id=member.agent_id,
                    agent_name=worker_name,
                    session_id=session_id,
                    user_message=_build_worker_user_message(data.message, member, dependency_outputs),
                    assistant_text=worker_text,
                    metadata={
                        "teamId": data.team_id,
                        "teamName": data.team_name,
                        "leaderAgentId": data.agent_id,
                        "model": model_name,
                        "skillIds": [skill.id for skill in worker_skills],
                    },
                )
                if worker_memory_facts:
                    await queue.put(_sse_event("MEMORY_SUMMARY", {
                        "agentId": member.agent_id,
                        "agentName": worker_name,
                        "facts": [fact.model_dump(mode="json") for fact in worker_memory_facts],
                    }))
            await queue.put(_sse_event("TEAM_ACTIVITY", {
                "activities": [item.model_dump(by_alias=True) for item in team_activities],
            }))
        except Exception as exc:
            activity = _find_team_activity(team_activities, member.agent_id)
            if activity:
                _replace_team_activity(
                    team_activities,
                    activity.model_copy(update={
                        "status": "failed",
                        "detail": f"Worker 执行失败: {exc}",
                        "output": worker_text,
                        "thinking": worker_thinking,
                        "tool_calls": worker_tool_calls,
                    }),
                )
            _record_run_event(
                session_id=session_id,
                event_type="WORKER_FAILED",
                persist=not data.sandbox,
                scope="worker",
                agent_id=member.agent_id,
                agent_name=worker_name,
                team_activity_id=activity.id if activity else None,
                status="failed",
                title=f"Worker failed: {worker_name}",
                content=str(exc),
                payload={"toolCallCount": len(worker_tool_calls)},
            )
            await queue.put(_sse_event("TEAM_ACTIVITY", {
                "activities": [item.model_dump(by_alias=True) for item in team_activities],
            }))
        finally:
            await queue.put(None)

    pending: dict[str, ChatTeamMemberPayload] = {member.agent_id: member for member in workers}
    running: dict[asyncio.Task[None], str] = {}
    completed_outputs: dict[str, dict[str, str]] = {}
    failed_workers: set[str] = set()
    all_worker_ids = {member.agent_id for member in workers}

    def start_ready_workers() -> None:
        ready: list[ChatTeamMemberPayload] = []
        for member in list(pending.values()):
            dependencies = _member_dependency_ids(member, workers)
            invalid_dependencies = [dep for dep in dependencies if dep not in all_worker_ids]
            if invalid_dependencies:
                pending.pop(member.agent_id, None)
                activity = _find_team_activity(team_activities, member.agent_id)
                if activity:
                    _replace_team_activity(
                        team_activities,
                        activity.model_copy(update={
                            "status": "failed",
                            "detail": "依赖配置无效：找不到指定的上游 Worker。",
                            "wait_reason": "依赖配置无效：找不到指定的上游 Worker。",
                        }),
                    )
                failed_workers.add(member.agent_id)
                continue
            failed_dependencies = [dep for dep in dependencies if dep in failed_workers]
            if failed_dependencies:
                pending.pop(member.agent_id, None)
                activity = _find_team_activity(team_activities, member.agent_id)
                if activity:
                    _replace_team_activity(
                        team_activities,
                        activity.model_copy(update={
                            "status": "failed",
                            "detail": f"上游 Worker 失败，跳过执行：{', '.join(_member_names(failed_dependencies, workers))}",
                            "wait_reason": f"上游 Worker 失败：{', '.join(_member_names(failed_dependencies, workers))}",
                        }),
                    )
                failed_workers.add(member.agent_id)
                continue
            blocking = [dep for dep in dependencies if dep not in completed_outputs and dep not in failed_workers]
            if blocking:
                activity = _find_team_activity(team_activities, member.agent_id)
                if activity:
                    _replace_team_activity(
                        team_activities,
                        activity.model_copy(update={
                            "status": "pending",
                            "detail": f"等待依赖 Worker 完成：{', '.join(_member_names(blocking, workers))}",
                            "wait_reason": f"等待依赖 Worker 完成：{', '.join(_member_names(blocking, workers))}",
                        }),
                    )
                continue
            ready.append(member)

        for member in ready:
            pending.pop(member.agent_id, None)
            dependencies = _member_dependency_ids(member, workers)
            dependency_outputs = [
                completed_outputs[dep]
                for dep in dependencies
                if dep in completed_outputs
            ]
            activity = _find_team_activity(team_activities, member.agent_id)
            if activity:
                _replace_team_activity(
                    team_activities,
                    activity.model_copy(update={
                        "status": "running",
                        "wait_reason": "",
                        "dependency_output_count": len(dependency_outputs),
                        "detail": (
                            f"依赖已满足，开始执行。已接收 {len(dependency_outputs)} 个上游输出。"
                            if dependency_outputs
                            else activity.detail
                        ),
                    }),
                )
            task = asyncio.create_task(run_worker(member, dependency_outputs))
            running[task] = member.agent_id

    start_ready_workers()
    await queue.put(_sse_event("TEAM_ACTIVITY", {
        "activities": [item.model_dump(by_alias=True) for item in team_activities],
    }))

    while running or pending:
        if not running and pending:
            for member in list(pending.values()):
                activity = _find_team_activity(team_activities, member.agent_id)
                if activity:
                    _replace_team_activity(
                        team_activities,
                        activity.model_copy(update={
                            "status": "failed",
                            "detail": "依赖配置形成循环或上游 Worker 失败，无法继续调度。",
                            "wait_reason": "依赖配置形成循环或上游 Worker 失败。",
                        }),
                    )
                failed_workers.add(member.agent_id)
            pending.clear()
            yield _sse_event("TEAM_ACTIVITY", {
                "activities": [item.model_dump(by_alias=True) for item in team_activities],
            })
            break

        item = await queue.get()
        if item is None:
            done_tasks = [task for task in running if task.done()]
            for task in done_tasks:
                worker_id = running.pop(task)
                activity = _find_team_activity(team_activities, worker_id)
                if activity and activity.status == "completed":
                    completed_outputs[worker_id] = {
                        "agentId": activity.agent_id,
                        "name": activity.agent_name,
                        "output": activity.output,
                    }
                elif activity and activity.status in {"failed", "waiting"}:
                    failed_workers.add(worker_id)
            if pause_event.is_set():
                for task in running:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*running.keys(), return_exceptions=True)
                while not queue.empty():
                    queued = queue.get_nowait()
                    if queued is not None:
                        yield queued
                return
            start_ready_workers()
            yield _sse_event("TEAM_ACTIVITY", {
                "activities": [item.model_dump(by_alias=True) for item in team_activities],
            })
            continue
        yield item

    await asyncio.gather(*running.keys(), return_exceptions=True)


def _build_agent_memory_prompt(agent_id: str | None) -> str:
    """Build memory context for an Agent when its configuration enables it."""
    if not agent_id or agent_id == "default":
        return ""

    agent_config = get_registered_agent(agent_id)
    if not agent_config or not agent_config.memory_enabled:
        return ""

    return build_agent_memory_system_prompt(agent_id)


def _build_runtime_context_prompt() -> str:
    """Build concrete runtime context that should be stable for every model call."""

    shanghai_now = datetime.now(ZoneInfo("Asia/Shanghai"))
    utc_now = datetime.now(timezone.utc)
    return "\n".join(
        [
            "## Runtime Context",
            f"Current date/time: {shanghai_now.strftime('%Y-%m-%d %H:%M:%S %Z')} (Asia/Shanghai).",
            f"Current UTC time: {utc_now.strftime('%Y-%m-%d %H:%M:%S UTC')}.",
            'Treat the Asia/Shanghai date above as "today" unless the user specifies another timezone.',
            "For current-events or web-search tasks, include the current year/date when useful, prefer recent sources, and verify freshness before answering.",
        ]
    )


def _build_clarification_system_prompt() -> str:
    """Instructions for asking the user for missing information."""

    return "\n".join([
        "## Clarification Contract",
        "If the user's request cannot be executed reliably because required information is missing, ask for clarification instead of guessing.",
        "When clarification is required, the first output token must be <clarification>. Output only one clarification block and no other text before or after it:",
        '<clarification>{"title":"需要补充信息","description":"简短说明为什么需要这些信息","questions":[{"id":"target","type":"choice","label":"请选择目标","options":["选项 A","选项 B"]},{"id":"needs","type":"multi_choice","label":"请选择需要的能力","options":["能力 A","能力 B"]},{"id":"details","type":"text","label":"请补充具体要求","placeholder":"输入补充信息"}]}</clarification>',
        "Supported question types are: choice, multi_choice, text.",
        "Use at most 3 questions. For choice and multi_choice, provide 2-5 concise options.",
        "Only use clarification when the missing information blocks meaningful progress.",
    ])


def _extract_clarification_request(text: str) -> dict | None:
    """Extract a structured clarification request from assistant text."""

    match = re.search(r"<clarification>\s*(.*?)\s*</clarification>", text, re.DOTALL)
    raw_text = match.group(1) if match else _extract_raw_clarification_json(text)
    if not raw_text:
        return None

    try:
        raw = json.loads(raw_text)
    except json.JSONDecodeError:
        return None

    questions: list[dict[str, object]] = []
    for index, item in enumerate(raw.get("questions") or []):
        if not isinstance(item, dict):
            continue
        question_type = str(item.get("type") or "").strip()
        if question_type not in {"choice", "multi_choice", "text"}:
            continue
        question_id = str(item.get("id") or f"q{index + 1}").strip()
        label = str(item.get("label") or item.get("question") or "").strip()
        if not question_id or not label:
            continue
        options = [
            str(option).strip()
            for option in (item.get("options") or [])
            if str(option).strip()
        ][:5]
        if question_type in {"choice", "multi_choice"} and len(options) < 2:
            continue
        questions.append({
            "id": question_id,
            "type": question_type,
            "label": label,
            "options": options,
            "placeholder": str(item.get("placeholder") or "").strip(),
            "required": bool(item.get("required", True)),
        })

    if not questions:
        return None

    return {
        "id": uuid.uuid4().hex,
        "title": str(raw.get("title") or "需要补充信息").strip(),
        "description": str(raw.get("description") or "").strip(),
        "questions": questions[:3],
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }


def _extract_raw_clarification_json(text: str) -> str | None:
    """Extract a bare clarification JSON object when the model omits tags."""

    if '"questions"' not in text or '"title"' not in text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    return text[start:end + 1]


def _parse_worker_selection(text: str, allowed_agent_ids: set[str]) -> tuple[list[str], str]:
    """Parse model JSON for worker selection and filter unknown ids."""

    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?", "", raw, flags=re.IGNORECASE).strip()
        raw = re.sub(r"```$", "", raw).strip()
    if "{" in raw and "}" in raw:
        raw = raw[raw.find("{"): raw.rfind("}") + 1]

    try:
        data = json.loads(raw)
    except Exception:
        return [], "模型未返回有效选择，保持人工选择。"

    raw_ids = data.get("selectedAgentIds") or data.get("selected_agent_ids") or []
    selected: list[str] = []
    if isinstance(raw_ids, list):
        for item in raw_ids:
            agent_id = str(item).strip()
            if agent_id in allowed_agent_ids and agent_id not in selected:
                selected.append(agent_id)

    reason = str(data.get("reason") or "").strip()
    return selected, reason


def _build_agent_plan_prompt(agent_config: object | None) -> str:
    """Build runtime planning instructions from the Agent plan config."""

    plan_config = getattr(agent_config, "plan_config", None)
    if not plan_config or not getattr(plan_config, "enabled", True):
        return ""

    mode = str(getattr(plan_config, "mode", "structured") or "structured")
    max_steps = int(getattr(plan_config, "max_steps", 6) or 6)
    require_before_tools = bool(getattr(plan_config, "require_plan_before_tools", True))
    require_user_approval = bool(getattr(plan_config, "require_user_approval", False))
    allow_replanning = bool(getattr(plan_config, "allow_replanning", True))

    lines = [
        "## Agent Planning Contract",
        "You have an explicit planning layer before and during execution.",
        f"Planning mode: {mode}.",
        f"Maximum plan steps: {max_steps}.",
        "Represent plans as concise numbered steps with status markers: pending, active, completed, blocked.",
        'Do not repeatedly output headings such as "Execution" after each tool call; keep tool-loop narration compact.',
    ]
    if require_before_tools:
        lines.append("Before calling any tool or MCP, first produce a short plan unless the task is trivial and purely conversational.")
    if require_user_approval:
        lines.append("After drafting the initial plan, ask the user to approve or adjust it before executing non-read-only actions.")
    if allow_replanning:
        lines.append("When tool results materially change the situation, update the plan briefly before continuing.")
    else:
        lines.append("Do not change the plan unless the user explicitly asks you to.")

    if mode == "lightweight":
        lines.append("Use at most 3 plan steps and keep the plan compact.")
    elif mode == "strict":
        lines.extend([
            "For complex tasks, provide one concise initial plan and one final result summary.",
            "Do not mark a step completed until the required tool result or reasoning evidence is available.",
            "If a step is blocked, stop and report the blocker instead of silently skipping it.",
        ])
    else:
        lines.extend([
            "For multi-step work, show the initial plan, then continue with execution.",
            "Keep planning visible but concise; avoid long meta explanations.",
        ])

    return "\n".join(lines)


def _build_team_system_prompt(
    team_name: str | None,
    members: list[ChatTeamMemberPayload],
) -> str:
    """Build Team context for the leader Agent."""
    if not team_name and not members:
        return ""

    lines = [
        "## Team Runtime Context",
        f"You are acting as the leader of Team: {team_name or 'Unnamed Team'}.",
        "Coordinate the worker agents according to their roles.",
        "Worker agents are executed in independent ReAct loops before your final response. Use their outputs as grounded team context and produce the final leader response.",
        "Available team members:",
    ]
    for member in members:
        dependency_note = (
            f" depends on {', '.join(member.depends_on)}"
            if member.role != "leader" and member.depends_on
            else ""
        )
        lines.append(
            f"- {member.role}: {member.name} ({member.agent_id})"
            + dependency_note
            + (f" - {member.description}" if member.description else "")
        )
    return "\n".join(lines)


def _build_worker_system_prompt(
    team_name: str | None,
    leader_name: str | None,
    member: ChatTeamMemberPayload,
) -> str:
    """Build Team context for a worker Agent."""
    return "\n".join([
        "## Team Worker Runtime Context",
        f"You are a worker agent in Team: {team_name or 'Unnamed Team'}.",
        f"Leader: {leader_name or 'Leader'}.",
        f"Your team role: {member.name}.",
        "Run your own ReAct loop. Use tools when needed, stay within the shared workspace context, and return a focused contribution for the leader.",
    ])


def _build_worker_user_message(
    user_message: str,
    member: ChatTeamMemberPayload,
    dependency_outputs: list[dict[str, str]] | None = None,
) -> str:
    """Build the delegated task sent to a worker."""
    parts = [
        "Leader delegated this user request to you:",
        user_message,
        "Your assignment:",
        member.description or "Analyze this request from your role and provide actionable findings.",
    ]
    if member.handoff_prompt:
        parts.extend([
            "Handoff instructions:",
            member.handoff_prompt,
        ])
    if dependency_outputs:
        parts.append("Upstream worker outputs you must use:")
        for item in dependency_outputs:
            parts.append(f"### {item.get('name') or item.get('agentId')}")
            parts.append(item.get("output") or "(No output)")
    parts.append("Return your worker result clearly. Do not write the final team answer; the leader will synthesize it.")
    return "\n\n".join(parts)


def _member_dependency_ids(
    member: ChatTeamMemberPayload,
    workers: list[ChatTeamMemberPayload],
) -> list[str]:
    """Normalize dependency identifiers to worker agent ids."""
    if member.execution_mode != "depends_on":
        return []

    by_member_id = {getattr(worker, "id", ""): worker.agent_id for worker in workers}
    by_agent_id = {worker.agent_id: worker.agent_id for worker in workers}
    normalized: list[str] = []
    for raw_id in member.depends_on:
        dep_id = by_agent_id.get(raw_id) or by_member_id.get(raw_id) or raw_id
        if dep_id and dep_id != member.agent_id and dep_id not in normalized:
            normalized.append(dep_id)
    return normalized


def _expand_selected_workers_with_dependencies(
    selected_agent_ids: list[str],
    workers: list[ChatTeamMemberPayload],
) -> list[str]:
    """Ensure a selected downstream worker also runs its upstream dependencies."""
    worker_by_agent_id = {worker.agent_id: worker for worker in workers}
    expanded: list[str] = []
    visited: set[str] = set()

    def add_with_dependencies(agent_id: str) -> None:
        if agent_id in visited or agent_id not in worker_by_agent_id:
            return
        visited.add(agent_id)
        member = worker_by_agent_id[agent_id]
        for dep_id in _member_dependency_ids(member, workers):
            add_with_dependencies(dep_id)
        expanded.append(agent_id)

    for agent_id in selected_agent_ids:
        add_with_dependencies(agent_id)

    return expanded


def _member_names(
    agent_ids: list[str],
    workers: list[ChatTeamMemberPayload],
) -> list[str]:
    names = []
    for agent_id in agent_ids:
        worker = next((item for item in workers if item.agent_id == agent_id), None)
        names.append(worker.name if worker else agent_id)
    return names


def _build_worker_outputs_system_prompt(worker_outputs: list[dict[str, str]]) -> str:
    """Build a leader-visible summary of actual worker outputs."""
    lines = [
        "## Worker ReAct Outputs",
        "The following worker outputs are authoritative context for your final team response.",
        "You must synthesize completed worker outputs explicitly. If a worker failed or is waiting, say what data is missing instead of inventing details.",
    ]
    for item in worker_outputs:
        status = item.get("status") or "unknown"
        detail = item.get("detail") or ""
        lines.append(f"### {item['name']} ({status})")
        if detail:
            lines.append(f"Status detail: {detail}")
        lines.append(item["output"] or "(No output)")
    return "\n\n".join(lines)


def _build_leader_user_message(user_message: str, worker_outputs: list[dict[str, str]]) -> str:
    """Build the final leader task with worker outputs in the user-visible turn."""
    if not worker_outputs:
        return user_message

    return "\n\n".join([
        "Original user request:",
        user_message,
        _build_worker_outputs_system_prompt(worker_outputs),
        "Leader instruction:",
        "Produce the final answer based on the worker outputs above. Mention the useful worker findings and do not ignore them.",
    ])


def _collect_worker_outputs(activities: list[ChatTeamActivity]) -> list[dict[str, str]]:
    """Collect worker states for the leader."""
    return [
        {
            "agentId": activity.agent_id,
            "name": activity.agent_name,
            "status": activity.status,
            "detail": activity.detail,
            "output": activity.output,
        }
        for activity in activities
        if activity.role != "leader"
        and activity.status in {"completed", "failed", "waiting"}
    ]


def _build_team_activities(
    *,
    team_name: str | None,
    leader_agent_id: str,
    leader_agent_name: str,
    members: list[ChatTeamMemberPayload],
    user_message: str,
) -> list[ChatTeamActivity]:
    """Create UI-visible Team activity records for the current run."""
    if not team_name and not members:
        return []

    now = datetime.now(timezone.utc).isoformat()
    summary = _make_session_title(user_message)
    activities = [
        ChatTeamActivity(
            id=f"team-{uuid.uuid4().hex}",
            agentId=leader_agent_id,
            agentName=leader_agent_name,
            role="leader",
            status="running",
            task=f"拆解任务并汇总 Team: {team_name or 'Unnamed Team'}",
            detail=f"正在组织成员围绕「{summary}」协作，并生成最终回复。",
            executionMode="leader",
            createdAt=now,
        )
    ]

    for member in members:
        if member.role == "leader" or member.agent_id == leader_agent_id:
            continue
        status = "running" if member.role == "worker" else "pending"
        activities.append(ChatTeamActivity(
            id=f"team-{uuid.uuid4().hex}",
            agentId=member.agent_id,
            agentName=member.name,
            role=member.role,
            status=status,
            task=_make_member_task(member, summary),
            detail=member.description or "等待 Leader 协调上下文。",
            output="",
            thinking="",
            executionMode=member.execution_mode,
            dependsOn=member.depends_on,
            waitReason=(
                f"等待依赖 Worker 完成：{', '.join(member.depends_on)}"
                if member.execution_mode == "depends_on" and member.depends_on
                else ""
            ),
            createdAt=now,
        ))
    return activities


def _make_member_task(member: ChatTeamMemberPayload, summary: str) -> str:
    """Generate a compact task label for a Team member."""
    if member.role == "leader":
        return f"作为 Leader 汇总「{summary}」"
    if member.description:
        return f"按角色分析「{summary}」"
    return f"处理分配任务「{summary}」"


def _set_team_activity_status(
    activities: list[ChatTeamActivity],
    status: str,
) -> list[ChatTeamActivity]:
    """Return Team activities with a unified terminal status."""
    return [
        item.model_copy(update={"status": status})
        for item in activities
    ]


def _finalize_team_activities(
    activities: list[ChatTeamActivity],
    *,
    leader_agent_id: str,
    leader_output: str,
) -> list[ChatTeamActivity]:
    """Mark normal Team activities complete while preserving failed/waiting states."""
    finalized: list[ChatTeamActivity] = []
    for item in activities:
        if item.status in {"failed", "waiting"}:
            finalized.append(item)
            continue
        if item.agent_id == leader_agent_id or item.role == "leader":
            finalized.append(item.model_copy(update={
                "status": "completed",
                "detail": "Leader 已完成最终汇总。",
                "output": leader_output,
            }))
        else:
            finalized.append(item.model_copy(update={"status": "completed"}))

    activities[:] = finalized
    return finalized


def _mark_team_leader_waiting_for_clarification(
    activities: list[ChatTeamActivity],
    leader_agent_id: str,
) -> None:
    """Keep Team state non-terminal when the leader needs user input."""
    if not activities:
        return
    updated: list[ChatTeamActivity] = []
    for item in activities:
        if item.agent_id == leader_agent_id or item.role == "leader":
            updated.append(item.model_copy(update={
                "status": "waiting",
                "detail": "Leader 需要用户补充信息后继续。",
            }))
        else:
            updated.append(item)
    activities[:] = updated


def _set_team_worker_status(
    activities: list[ChatTeamActivity],
    status: str,
) -> list[ChatTeamActivity]:
    """Return Team activities with all worker records moved to a new status."""
    return [
        item.model_copy(update={"status": status})
        if item.role != "leader"
        else item
        for item in activities
    ]


def _update_worker_tool_calls(
    tool_calls: list[ChatToolRun],
    event_type: str,
    event_data: dict,
) -> list[ChatToolRun]:
    """Update worker tool call records from AgentScope tool events."""
    tool_call_id = str(event_data.get("tool_call_id") or "")
    if not tool_call_id:
        return tool_calls

    next_calls = [item.model_copy() for item in tool_calls]
    index = next((idx for idx, item in enumerate(next_calls) if item.id == tool_call_id), -1)

    if event_type == "TOOL_CALL_START":
        if index == -1:
            next_calls.append(ChatToolRun(
                id=tool_call_id,
                name=str(event_data.get("tool_call_name") or "tool"),
                status="calling",
            ))
        return next_calls

    if index == -1:
        next_calls.append(ChatToolRun(
            id=tool_call_id,
            name=str(event_data.get("tool_call_name") or "tool"),
            status="calling",
        ))
        index = len(next_calls) - 1

    current = next_calls[index]
    if event_type == "TOOL_CALL_DELTA":
        next_calls[index] = current.model_copy(update={
            "args": current.args + str(event_data.get("delta") or ""),
        })
    elif event_type in {"TOOL_CALL_END", "TOOL_RESULT_START"}:
        next_calls[index] = current.model_copy(update={"status": "running"})
    elif event_type == "TOOL_RESULT_TEXT_DELTA":
        next_calls[index] = current.model_copy(update={
            "result": current.result + str(event_data.get("delta") or ""),
            "status": "running",
        })
    elif event_type == "TOOL_RESULT_END":
        next_calls[index] = current.model_copy(update={"status": "completed"})
    else:
        return tool_calls

    return next_calls


def _find_team_activity(
    activities: list[ChatTeamActivity],
    agent_id: str,
) -> ChatTeamActivity | None:
    """Find one Team activity by Agent id."""
    return next((item for item in activities if item.agent_id == agent_id), None)


def _replace_team_activity(
    activities: list[ChatTeamActivity],
    next_activity: ChatTeamActivity,
) -> None:
    """Replace one Team activity in-place."""
    for index, activity in enumerate(activities):
        if activity.id == next_activity.id:
            activities[index] = next_activity
            return


def _trim_text(text: str, limit: int) -> str:
    """Trim text for compact activity cards."""
    compact = " ".join(text.strip().split())
    if len(compact) <= limit:
        return compact
    return compact[:limit] + "..."


def _set_skill_run_status(
    skill_runs: list[ChatSkillRun],
    status: str,
) -> list[ChatSkillRun]:
    """返回更新状态后的 Skill run 列表。"""
    return [
        item.model_copy(update={"status": status})
        for item in skill_runs
    ]
