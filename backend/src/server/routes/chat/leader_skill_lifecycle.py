"""Leader 流中的 AgentScope Skill viewer 生命周期转换。"""

import json
from typing import Any

from src.server.routes.chat.helpers import source_event
from src.server.routes.chat.models import ChatSkillRun, ChatToolRun
from src.server.routes.chat.runtime_events import runtime_trace_source_event
from src.server.routes.chat.skill_runs import start_or_resume_skill_run
from src.server.routes.skills import Skill, list_skill_usage_details, resolve_installed_skills


def skill_lifecycle_events(
    *,
    event_type: str,
    event_data: dict,
    state: Any,
    skill_runs: list[ChatSkillRun],
    session_id: str,
    persist: bool,
    agent_id: str,
    agent_name: str,
) -> list[str]:
    """把 AgentScope Skill viewer 工具事件转换为平台 Skill 生命周期。"""

    tool_call_id = str(event_data.get("tool_call_id") or "")
    tool_call = next((item for item in state.assistant_tool_calls if item.id == tool_call_id), None)
    tool_name = str(event_data.get("tool_call_name") or getattr(tool_call, "name", ""))
    if tool_name != "Skill":
        return []

    if event_type == "TOOL_CALL_START":
        payload = {"id": tool_call_id, "name": "Skill", "status": "running"}
        return [runtime_trace_source_event(
            "SKILL_STARTED",
            session_id=session_id,
            scope="skill",
            source="agentscope",
            source_event_type=event_type,
            agent_id=agent_id,
            agent_name=agent_name,
            status="running",
            payload=payload,
            collect=state.runtime_events,
        )]

    if event_type != "TOOL_RESULT_END":
        return []

    requested_name = skill_name_from_tool_call(tool_call)
    matches = [item for item in state.available_skill_details
               if str(item.get("loadName") or item.get("name") or "") == requested_name]
    detail = matches[0] if len(matches) == 1 else None
    failed = str(event_data.get("state") or "").lower() in {"error", "failed", "denied"} or detail is None
    if failed:
        payload = {
            "id": tool_call_id,
            "name": requested_name or "Skill",
            "status": "failed",
        }
        return [
            runtime_trace_source_event(
                "SKILL_FAILED",
                session_id=session_id,
                scope="skill",
                source="agentscope",
                source_event_type=event_type,
                agent_id=agent_id,
                agent_name=agent_name,
                status="failed",
                payload=payload,
                collect=state.runtime_events,
            ),
            source_event("SKILL_END", {"skills": [payload], "status": "failed"}),
        ]

    skill_run = ChatSkillRun.model_validate({**detail, "status": "running"})
    if all(item.id != skill_run.id for item in skill_runs):
        skill_runs.append(skill_run)
    if persist:
        skill = Skill.model_validate({
            **detail,
            "category": "runtime",
            "tags": [],
            "status": "installed",
            "enabled": True,
            "updatedAt": "",
        })
        state.active_skill_run = start_or_resume_skill_run(
            session_id=session_id,
            agent_id=agent_id,
            skill=skill,
            status="active",
            current_step="instructions_loaded",
        )
    payload = skill_run.model_dump(by_alias=True)
    return [
        runtime_trace_source_event(
            "SKILL_CONTEXT",
            session_id=session_id,
            scope="skill",
            source="agentscope",
            source_event_type=event_type,
            agent_id=agent_id,
            agent_name=agent_name,
            status="running",
            payload=payload,
            collect=state.runtime_events,
        ),
        source_event("SKILL_CONTEXT", {"skills": [payload]}),
    ]


def skill_name_from_tool_call(tool_call: ChatToolRun | None) -> str:
    """从 Skill viewer 的流式 JSON 参数中提取 Skill 名称。"""

    if tool_call is None or not tool_call.args:
        return ""
    try:
        value = json.loads(tool_call.args)
    except json.JSONDecodeError:
        return ""
    return str(value.get("skill") or "") if isinstance(value, dict) else ""
