"""Leader Agent 事件流处理。"""

from dataclasses import dataclass, field
from typing import Any, AsyncGenerator

from src.server.routes.chat.helpers import _set_skill_run_status, _sse_event
from src.server.routes.chat.hitl import _create_pending_interaction, _hitl_payload
from src.server.routes.chat.models import (
    HITL_EVENT_TYPES,
    ChatModelCacheRun,
    ChatSkillRun,
    ChatTeamActivity,
    ChatToolRun,
)
from src.server.routes.chat.plans import mark_plan_tool_event, serialize_plan_for_event
from src.server.routes.chat.prompts import _extract_clarification_request
from src.server.routes.chat.skill_runs import update_skill_run_status
from src.server.routes.chat.team_state import (
    _mark_team_leader_waiting_for_clarification,
    _update_worker_tool_calls,
)
from src.server.routes.chat.trace_recorder import (
    _attach_provider_cache_usage,
    _model_cache_from_event,
    _record_agent_scope_event,
    _record_permission_decision,
    _record_run_event,
    _record_tool_execution_artifacts,
)
from src.storage.database import get_plan_record
from src.tools.plan_runtime import PLAN_TOOL_NAMES


@dataclass
class LeaderStreamState:
    """Leader Agent 流式执行中累计的状态。"""

    assistant_text: str = ""
    assistant_thinking: str = ""
    assistant_tool_calls: list[ChatToolRun] = field(default_factory=list)
    assistant_model_caches: list[ChatModelCacheRun] = field(default_factory=list)
    runtime_plan: dict[str, Any] | None = None
    active_skill_run: dict | None = None
    stopped: bool = False
    stop_reason: str = ""


def is_plan_runtime_tool_name(name: str) -> bool:
    """判断工具名是否为 Plan Runtime 内部工具。"""

    normalized = name.replace(" ", "").replace("-", "").replace("_", "").lower()
    return any(
        normalized == tool_name.lower()
        or normalized.endswith(tool_name.lower())
        for tool_name in PLAN_TOOL_NAMES
    )


async def stream_leader_agent_reply(
    *,
    agent: object,
    user_msg: object,
    state: LeaderStreamState,
    session_id: str,
    persist: bool,
    agent_id: str,
    agent_name: str,
    model_name: str,
    guardrails: object,
    message: str,
    skill_runs: list[ChatSkillRun],
    team_activities: list[ChatTeamActivity],
) -> AsyncGenerator[str, None]:
    """消费 Leader AgentScope 事件流，并处理 HITL / Plan / clarification 早停。"""

    async for event in agent.reply_stream(user_msg):
        event_data = event.model_dump(mode="json")
        event_type = event_data.pop("type", "UNKNOWN")
        tool_name = str(
            event_data.get("tool_call_name")
            or event_data.get("name")
            or event_data.get("tool_name")
            or "tool"
        )
        _attach_provider_cache_usage(event_type, event_data)
        if event_type == "MODEL_CALL_END":
            cache_entry = _model_cache_from_event(event_data, model_name)
            if cache_entry:
                state.assistant_model_caches.append(cache_entry)

        updated_plan = mark_plan_tool_event(
            state.runtime_plan,
            event_type=event_type,
            tool_name=tool_name,
        )
        if updated_plan and updated_plan != state.runtime_plan:
            state.runtime_plan = updated_plan
            yield _sse_event("PLAN_UPDATE", {
                "plan": serialize_plan_for_event(state.runtime_plan),
            })
        if state.runtime_plan and event_type in {"TOOL_CALL_END", "TOOL_RESULT_END"}:
            refreshed_plan = get_plan_record(str(state.runtime_plan.get("id")))
            if refreshed_plan and refreshed_plan != state.runtime_plan:
                state.runtime_plan = refreshed_plan
                yield _sse_event("PLAN_UPDATE", {
                    "plan": serialize_plan_for_event(state.runtime_plan),
                })

        _record_agent_scope_event(
            session_id=session_id,
            event_type=event_type,
            event_data=event_data,
            persist=persist,
            scope="leader",
            agent_id=agent_id,
            agent_name=agent_name,
        )
        _record_permission_decision(
            session_id=session_id,
            event_type=event_type,
            event_data=event_data,
            persist=persist,
            scope="leader",
            agent_id=agent_id,
            agent_name=agent_name,
            guardrails=guardrails,
        )
        next_tool_calls = _update_worker_tool_calls(
            state.assistant_tool_calls,
            event_type,
            event_data,
        )
        if next_tool_calls is not state.assistant_tool_calls:
            state.assistant_tool_calls = next_tool_calls
        _record_tool_execution_artifacts(
            session_id=session_id,
            event_type=event_type,
            event_data=event_data,
            persist=persist,
            scope="leader",
            agent_id=agent_id,
            agent_name=agent_name,
            team_activity_id=None,
            guardrails=guardrails,
            tool_calls=state.assistant_tool_calls,
        )

        if event_type in HITL_EVENT_TYPES:
            if state.active_skill_run and persist:
                state.active_skill_run = update_skill_run_status(
                    state.active_skill_run,
                    status="waiting_user",
                    current_step="waiting_hitl_resume",
                )
            interaction = _create_pending_interaction(
                interaction_type=event_type,
                agent=agent,
                session_id=session_id,
                agent_id=agent_id,
                agent_name=agent_name,
                model_name=model_name,
                reply_id=str(event_data.get("reply_id") or ""),
                tool_calls=list(event_data.get("tool_calls") or []),
                message=message,
                assistant_text=state.assistant_text,
                assistant_tool_calls=state.assistant_tool_calls,
                skill_runs=skill_runs,
                team_activities=team_activities,
                persist=persist,
            )
            yield _sse_event("HITL_REQUIRED", _hitl_payload(interaction))
            state.stopped = True
            state.stop_reason = "hitl"
            return

        if event_type == "TEXT_BLOCK_DELTA":
            state.assistant_text += str(event_data.get("delta", ""))
        elif event_type == "THINKING_BLOCK_DELTA":
            state.assistant_thinking += str(event_data.get("delta", ""))

        if event_type == "REPLY_END" and skill_runs:
            completed_skill_runs = _set_skill_run_status(skill_runs, "completed")
            yield _sse_event("SKILL_END", {
                "skills": [item.model_dump(by_alias=True) for item in completed_skill_runs],
                "status": "completed",
            })

        yield _sse_event(event_type, event_data)

    clarification = _extract_clarification_request(state.assistant_text) or _extract_clarification_request(state.assistant_thinking)
    if clarification:
        if state.active_skill_run:
            state.active_skill_run = update_skill_run_status(
                state.active_skill_run,
                status="waiting_user",
                current_step=str(clarification.get("title") or "waiting_user"),
                pending_interaction_id=str(clarification.get("id") or ""),
            )
        _mark_team_leader_waiting_for_clarification(team_activities, agent_id)
        if team_activities:
            yield _sse_event("TEAM_ACTIVITY", {
                "activities": [item.model_dump(by_alias=True) for item in team_activities],
            })
        _record_run_event(
            session_id=session_id,
            event_type="CLARIFICATION_REQUIRED",
            persist=persist,
            scope="leader",
            agent_id=agent_id,
            agent_name=agent_name,
            status="waiting",
            title=str(clarification.get("title") or "Clarification required"),
            payload=clarification,
        )
        yield _sse_event("CLARIFICATION_REQUIRED", clarification)
        state.stopped = True
        state.stop_reason = "clarification"
