"""SkillRun lifecycle helpers for multi-turn Skills."""

from __future__ import annotations

from typing import Any

from src.server.routes.skills import Skill, get_installed_skill
from src.storage.database import get_active_skill_run, get_skill_run_by_pending_interaction, upsert_skill_run


ACTIVE_STATUSES = {"active", "waiting_user"}


def get_active_session_skill_run(session_id: str, agent_id: str) -> dict[str, Any] | None:
    """Return the latest active/waiting SkillRun for one Agent in one session."""

    if not session_id or not agent_id:
        return None
    return get_active_skill_run(session_id, agent_id)


def get_skill_run_for_pending_interaction(interaction_id: str) -> dict[str, Any] | None:
    """Return the SkillRun that was waiting on a specific HITL interaction."""

    if not interaction_id:
        return None
    return get_skill_run_by_pending_interaction(interaction_id)


def start_or_resume_skill_run(
    *,
    session_id: str,
    agent_id: str,
    skill: Skill,
    status: str = "active",
    current_step: str = "",
    collected_inputs: dict[str, Any] | None = None,
    pending_interaction_id: str = "",
) -> dict[str, Any]:
    """Create or update the session-level SkillRun for a selected Skill."""

    existing = get_active_session_skill_run(session_id, agent_id)
    run_id = existing["id"] if existing and existing.get("skillId") == skill.id else ""
    return upsert_skill_run({
        "id": run_id,
        "sessionId": session_id,
        "agentId": agent_id,
        "skillId": skill.id,
        "skillName": skill.name,
        "status": status,
        "currentStep": current_step or (existing or {}).get("currentStep", ""),
        "collectedInputs": {
            **(collected_inputs if collected_inputs is not None else (existing or {}).get("collectedInputs", {})),
            "_skillSource": {"id": skill.id, "loadName": skill.load_name, "sourcePath": skill.source_path,
                             "contentHash": skill.content_hash},
        },
        "pendingInteractionId": pending_interaction_id,
        "createdAt": (existing or {}).get("createdAt", ""),
    })


def update_skill_run_status(
    run: dict[str, Any] | None,
    *,
    status: str,
    current_step: str = "",
    collected_inputs: dict[str, Any] | None = None,
    pending_interaction_id: str = "",
) -> dict[str, Any] | None:
    """Update an existing SkillRun status."""

    if not run:
        return None
    return upsert_skill_run({
        **run,
        "status": status,
        "currentStep": current_step or run.get("currentStep", ""),
        "collectedInputs": collected_inputs if collected_inputs is not None else run.get("collectedInputs", {}),
        "pendingInteractionId": pending_interaction_id,
    })


def get_skill_for_run(run: dict[str, Any] | None) -> Skill | None:
    """Resolve the Skill referenced by a SkillRun."""

    if not run:
        return None
    return get_installed_skill(str(run.get("skillId") or ""))


def build_skill_run_system_prompt(run: dict[str, Any] | None) -> str:
    """Build continuation instructions for an active multi-turn Skill."""

    if not run:
        return ""

    lines = [
        "## Active SkillRun",
        "A multi-turn Skill is active in this conversation. Continue this SkillRun before considering any new Skill.",
        f"- skillRunId: {run.get('id', '')}",
        f"- skillId: {run.get('skillId', '')}",
        f"- skillName: {run.get('skillName', '')}",
        f"- status: {run.get('status', '')}",
        f"- currentStep: {run.get('currentStep', '') or 'not recorded'}",
        f"- collectedInputs: {run.get('collectedInputs', {})}",
        "",
        "Rules:",
        "- Treat the user's latest message as input for this active SkillRun.",
        "- If required information is still missing, use the Clarification Contract to ask the next focused question.",
        "- Do not restart the Skill from the beginning unless the user explicitly asks to cancel or restart.",
        "- When the Skill has produced its final deliverable, clearly say the Skill work is complete.",
    ]
    return "\n".join(lines)


def should_complete_skill_run(assistant_text: str) -> bool:
    """Best-effort completion heuristic for MVP multi-turn Skills."""

    normalized = assistant_text.lower()
    completion_markers = (
        "skill work is complete",
        "brainstorming complete",
        "spec written",
        "设计已完成",
        "需求收集完成",
        "技能流程完成",
    )
    return any(marker in normalized for marker in completion_markers)
