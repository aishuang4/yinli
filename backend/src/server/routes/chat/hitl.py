"""HITL 人工审批 — pending interaction 的创建/清理/序列化与恢复事件构建。"""

import uuid

from agentscope.event import (
    ConfirmResult,
    ExternalExecutionResultEvent,
    UserConfirmResultEvent,
)
from agentscope.message import (
    ToolCallBlock,
    ToolResultBlock,
    ToolResultState,
)

from src.server.routes.chat.models import (
    PENDING_AGENTS,
    PENDING_INTERACTIONS,
    ChatResumeRequest,
    ChatSkillRun,
    ChatTeamActivity,
    ChatToolRun,
    PendingInteraction,
)
from src.server.routes.chat.background_runs import current_chat_background_run_id
from src.server.routes.chat.team_state import _find_team_activity
from src.server.routes.chat.trace_recorder import _record_run_event


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
    resume_tool_calls: list[dict] | None = None,
    assistant_tool_calls: list[ChatToolRun] | None = None,
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
        run_id=current_chat_background_run_id(),
        tool_calls=tool_calls,
        resume_tool_calls=tool_calls if resume_tool_calls is None else resume_tool_calls,
        message=message,
        assistant_text=assistant_text,
        assistant_tool_calls=assistant_tool_calls or [],
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


def _merge_hitl_tool_calls(
    event_tool_calls: list[dict],
    active_tool_calls: list[ChatToolRun],
) -> list[dict]:
    """合并 HITL 事件显式工具与同轮尚未完成的工具调用。"""

    merged = [dict(tool_call) for tool_call in event_tool_calls]
    seen_ids = {str(tool_call.get("id") or "") for tool_call in merged}
    for tool_call in active_tool_calls:
        if tool_call.status not in {"calling", "running"}:
            continue
        if not tool_call.id or tool_call.id in seen_ids:
            continue
        merged.append({
            "type": "tool_call",
            "id": tool_call.id,
            "name": tool_call.name or "tool",
            "input": tool_call.args,
            "state": "asking",
        })
        seen_ids.add(tool_call.id)
    return merged


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
        "runId": interaction.run_id,
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
    resume_tool_calls = (
        interaction.tool_calls
        if interaction.resume_tool_calls is None
        else interaction.resume_tool_calls
    )
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
                for tool_call in resume_tool_calls
            ],
        )

    if interaction.type == "REQUIRE_EXTERNAL_EXECUTION":
        return ExternalExecutionResultEvent(
            reply_id=interaction.reply_id,
            execution_results=[
                ToolResultBlock(
                    id=str(tool_call.get("id", "")),
                    name=str(tool_call.get("name", "tool")),
                    output=(
                        data.external_results.get(str(tool_call.get("id", "")), "")
                        if data.action == "approve"
                        else "用户拒绝或取消了这次外部执行/补充信息请求。"
                    ),
                    state=ToolResultState.SUCCESS if data.action == "approve" else ToolResultState.DENIED,
                )
                for tool_call in resume_tool_calls
            ],
        )

    raise ValueError(f"Unsupported interaction type: {interaction.type}")
