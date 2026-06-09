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
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncGenerator

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

from src.agents.factory import get_or_create_agent
from src.config.settings import settings
from src.server.routes.agents import get_registered_agent
from src.server.routes.memory import append_agent_memory_event, maybe_summarize_agent_run
from src.server.routes.skills import (
    build_skill_system_prompt_from_skills,
    list_skill_usage_details,
    select_relevant_skills,
)
from src.storage.database import (
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
PENDING_INTERACTIONS: dict[str, "PendingInteraction"] = {}
PENDING_AGENTS: dict[str, object] = {}


class ChatImagePayload(BaseModel):
    """用户上传图片"""

    name: str = ""
    media_type: str
    data: str


class ChatTeamMemberPayload(BaseModel):
    """前端传入的 Team 成员摘要"""

    agent_id: str = Field(alias="agentId")
    name: str
    role: str
    description: str = ""

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
        agent_name = data.agent_name or "Assistant"
        base_system_prompt = data.system_prompt or "You are a helpful AI assistant."
        team_system_prompt = _build_team_system_prompt(data.team_name, data.team_members)
        used_skills = select_relevant_skills(data.skill_ids, data.message)
        skill_system_prompt = build_skill_system_prompt_from_skills(used_skills)
        used_skill_details = list_skill_usage_details(used_skills)
        skill_runs = [
            ChatSkillRun.model_validate({**skill_detail, "status": "running"})
            for skill_detail in used_skill_details
        ]
        system_prompt = "\n\n".join(
            part for part in [base_system_prompt, team_system_prompt, skill_system_prompt] if part
        )
        model_name = data.model or settings.default_model
        assistant_text = ""
        assistant_error: str | None = None
        worker_outputs: list[dict[str, str]] = []
        team_activities = _build_team_activities(
            team_name=data.team_name,
            leader_agent_id=agent_id,
            leader_agent_name=agent_name,
            members=data.team_members,
            user_message=data.message,
        )

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
            },
        )

        yield _sse_event("SESSION", {
            "session_id": session_id,
        })

        if team_activities:
            yield _sse_event("TEAM_ACTIVITY", {
                "activities": [item.model_dump(by_alias=True) for item in team_activities],
            })

        if used_skill_details:
            yield _sse_event("SKILL_CONTEXT", {
                "skills": used_skill_details,
            })
            for skill_detail in used_skill_details:
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
        if not settings.dashscope_api_key:
            assistant_error = "DashScope API Key 未配置。请在 backend/.env 中设置 AGENT_WORKSTATION_DASHSCOPE_API_KEY"
            failed_skill_runs = _set_skill_run_status(skill_runs, "failed")
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

            skill_cache_key = ",".join(sorted(skill.id for skill in used_skills))
            runtime_agent_id = f"{agent_id}:{session_id}:{model_name}:thinking-{data.deep_thinking}:skills-{skill_cache_key}:permission-default"
            agent = get_or_create_agent(
                agent_id=runtime_agent_id,
                name=agent_name,
                system_prompt=system_prompt,
                model_name=model_name,
                thinking_enable=data.deep_thinking,
                permission_mode=PermissionMode.DEFAULT,
            )
        except Exception as e:
            assistant_error = f"Agent 创建失败: {str(e)}"
            failed_skill_runs = _set_skill_run_status(skill_runs, "failed")
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
        user_msg = UserMsg(name="user", content=_build_user_content(data.message, data.images))

        # 调用 AgentScope 真实 Agent 的 reply_stream
        try:
            async for event in agent.reply_stream(user_msg):
                # 将 Pydantic 事件模型序列化为 dict
                event_data = event.model_dump(mode="json")

                # 提取事件类型（EventType 枚举值）
                event_type = event_data.pop("type", "UNKNOWN")
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
                    )
                    yield _sse_event("HITL_REQUIRED", _hitl_payload(interaction))
                    return
                if event_type == "TEXT_BLOCK_DELTA":
                    assistant_text += str(event_data.get("delta", ""))

                if event_type == "REPLY_END" and skill_runs:
                    completed_skill_runs = _set_skill_run_status(skill_runs, "completed")
                    yield _sse_event("SKILL_END", {
                        "skills": [item.model_dump(by_alias=True) for item in completed_skill_runs],
                        "status": "completed",
                    })

                # 发送 SSE 事件
                yield _sse_event(event_type, event_data)

            completed_skill_runs = _set_skill_run_status(skill_runs, "completed")
            completed_team_activities = _set_team_activity_status(team_activities, "completed")
            _append_assistant_message(
                session_id=session_id,
                agent_id=agent_id,
                agent_name=agent_name,
                model_name=model_name,
                content=assistant_text,
                error=assistant_error,
                skill_calls=completed_skill_runs,
                team_activities=completed_team_activities,
            )
            if team_activities:
                yield _sse_event("TEAM_ACTIVITY", {
                    "activities": [
                        item.model_dump(by_alias=True)
                        for item in completed_team_activities
                    ],
                })
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
            memory_facts = maybe_summarize_agent_run(
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
                worker_memory_facts = maybe_summarize_agent_run(
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
                            "detail": "Worker 已完成，Leader 正在汇总最终回复。",
                        }),
                    )
                    yield _sse_event("TEAM_ACTIVITY", {
                        "activities": [item.model_dump(by_alias=True) for item in team_activities],
                    })

                user_msg = UserMsg(
                    name="user",
                    content=_build_user_content(chat_data.message, chat_data.images),
                )
                async for event in leader_agent.reply_stream(user_msg):
                    event_data = event.model_dump(mode="json")
                    event_type = event_data.pop("type", "UNKNOWN")
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
                completed_team_activities = _set_team_activity_status(team_activities, "completed")
                _append_assistant_message(
                    session_id=interaction.session_id,
                    agent_id=leader_agent_id,
                    agent_name=leader_agent_name,
                    model_name=interaction.model_name,
                    content=assistant_text,
                    error=assistant_error,
                    skill_calls=completed_skill_runs,
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
                memory_facts = maybe_summarize_agent_run(
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

            async for event in agent.reply_stream(resume_event):
                event_data = event.model_dump(mode="json")
                event_type = event_data.pop("type", "UNKNOWN")
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
            completed_team_activities = _set_team_activity_status(team_activities, "completed")
            _append_assistant_message(
                session_id=interaction.session_id,
                agent_id=interaction.agent_id,
                agent_name=interaction.agent_name,
                model_name=interaction.model_name,
                content=assistant_text,
                error=assistant_error,
                skill_calls=completed_skill_runs,
                team_activities=completed_team_activities,
            )
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
            memory_facts = maybe_summarize_agent_run(
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
    save_chat_store(store.model_dump(mode="json"))


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
            _replace_team_activity(team_activities, activity.model_copy(update={"status": "running"}))

    yield _sse_event("TEAM_ACTIVITY", {
        "activities": [item.model_dump(by_alias=True) for item in team_activities],
    })

    async def run_worker(member: ChatTeamMemberPayload) -> None:
        activity = _find_team_activity(team_activities, member.agent_id)
        if activity is None:
            return
        worker_config = get_registered_agent(member.agent_id)
        worker_name = worker_config.name if worker_config else member.name
        worker_prompt = worker_config.system_prompt if worker_config else member.description
        worker_skill_ids = worker_config.skills if worker_config else []
        worker_skills = select_relevant_skills(worker_skill_ids, data.message)
        worker_skill_prompt = build_skill_system_prompt_from_skills(worker_skills)
        worker_system_prompt = "\n\n".join(
            part
            for part in [
                worker_prompt or "You are a helpful worker agent.",
                _build_worker_system_prompt(data.team_name, data.agent_name, member),
                worker_skill_prompt,
            ]
            if part
        )
        worker_cache_key = ",".join(sorted(skill.id for skill in worker_skills))
        runtime_worker_id = (
            f"{member.agent_id}:{session_id}:{model_name}:team-worker:"
            f"thinking-{data.deep_thinking}:skills-{worker_cache_key}:permission-default"
        )
        worker_agent = get_or_create_agent(
            agent_id=runtime_worker_id,
            name=worker_name,
            system_prompt=worker_system_prompt,
            model_name=model_name,
            thinking_enable=data.deep_thinking,
            permission_mode=PermissionMode.DEFAULT,
        )
        worker_text = ""
        worker_thinking = ""
        worker_tool_calls: list[ChatToolRun] = []

        try:
            worker_msg = UserMsg(
                name="team_leader",
                content=_build_user_content(
                    _build_worker_user_message(data.message, member),
                    data.images,
                ),
            )
            async for event in worker_agent.reply_stream(worker_msg):
                event_data = event.model_dump(mode="json")
                event_type = event_data.pop("type", "UNKNOWN")
                if event_type in HITL_EVENT_TYPES:
                    activity = _find_team_activity(team_activities, member.agent_id)
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
                        message=_build_worker_user_message(data.message, member),
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
            worker_memory_facts = maybe_summarize_agent_run(
                agent_id=member.agent_id,
                agent_name=worker_name,
                session_id=session_id,
                user_message=_build_worker_user_message(data.message, member),
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
            await queue.put(_sse_event("TEAM_ACTIVITY", {
                "activities": [item.model_dump(by_alias=True) for item in team_activities],
            }))
        finally:
            await queue.put(None)

    tasks = [asyncio.create_task(run_worker(member)) for member in workers]
    remaining = len(tasks)
    while remaining:
        item = await queue.get()
        if item is None:
            remaining -= 1
            if pause_event.is_set():
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                while not queue.empty():
                    queued = queue.get_nowait()
                    if queued is not None:
                        yield queued
                return
            continue
        yield item

    await asyncio.gather(*tasks, return_exceptions=True)


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
        lines.append(
            f"- {member.role}: {member.name} ({member.agent_id})"
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


def _build_worker_user_message(user_message: str, member: ChatTeamMemberPayload) -> str:
    """Build the delegated task sent to a worker."""
    return "\n\n".join([
        "Leader delegated this user request to you:",
        user_message,
        "Your assignment:",
        member.description or "Analyze this request from your role and provide actionable findings.",
        "Return your worker result clearly. Do not write the final team answer; the leader will synthesize it.",
    ])


def _build_worker_outputs_system_prompt(worker_outputs: list[dict[str, str]]) -> str:
    """Build a leader-visible summary of actual worker outputs."""
    lines = ["## Worker ReAct Outputs"]
    for item in worker_outputs:
        lines.append(f"### {item['name']}")
        lines.append(item["output"] or "(No output)")
    return "\n\n".join(lines)


def _collect_worker_outputs(activities: list[ChatTeamActivity]) -> list[dict[str, str]]:
    """Collect completed worker outputs for the leader."""
    return [
        {
            "agentId": activity.agent_id,
            "name": activity.agent_name,
            "output": activity.output,
        }
        for activity in activities
        if activity.role != "leader"
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
