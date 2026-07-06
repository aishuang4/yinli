"""记忆模块 Trace 审计事件。

记忆审计统一写入独立 `MEMORY_AUDIT` trace event，payload.type 固定为 `memory`，
避免和普通模型、工具、Skill 运行事件混在一起。
"""

from __future__ import annotations

from typing import Any

MEMORY_AUDIT_EVENT_TYPE = "MEMORY_AUDIT"
MEMORY_SKILL_GENERATING_EVENT_TYPE = "MEMORY_SKILL_GENERATING"


def record_memory_audit_event(
    *,
    session_id: str,
    persist: bool,
    agent_id: str,
    agent_name: str,
    action: str,
    status: str,
    title: str,
    content: str = "",
    payload: dict[str, Any] | None = None,
) -> None:
    """写入单条记忆审计 Trace，失败不能影响主流程。"""

    if not persist:
        return
    try:
        from src.server.routes.chat.trace_recorder import _record_run_event

        audit_payload = {
            "type": "memory",
            "memoryType": action,
            **(payload or {}),
        }
        _record_run_event(
            session_id=session_id,
            event_type=MEMORY_AUDIT_EVENT_TYPE,
            persist=persist,
            scope="memory",
            agent_id=agent_id,
            agent_name=agent_name,
            status=status,
            title=title,
            content=content,
            payload=audit_payload,
        )
    except Exception:
        pass


def memory_reflection_audit_payload(result: Any) -> dict[str, Any]:
    """把 MemoryReflectionResult 压缩成 Trace payload。"""

    updates = list(getattr(result, "updates", []) or [])
    skill_record = getattr(result, "skill_evolution_record", None) or {}
    skill_candidate = getattr(result, "skill_candidate", None)
    scopes = sorted({str(getattr(update, "scope", "")) for update in updates if getattr(update, "scope", "")})
    operations = [
        {
            "scope": getattr(update, "scope", ""),
            "ownerId": getattr(update, "owner_id", ""),
            "section": getattr(update, "section", ""),
            "operation": getattr(update, "operation", ""),
            "path": getattr(update, "path", ""),
        }
        for update in updates[:12]
    ]
    return {
        "markdownUpdateCount": len(updates),
        "markdownScopes": scopes,
        "markdownOperations": operations,
        "skillCandidateKept": skill_candidate is not None,
        "skillEvolutionRecordId": str(skill_record.get("id") or ""),
        "skillEvolutionStatus": str(skill_record.get("status") or ""),
        "registeredSkillId": str(skill_record.get("registeredSkillId") or ""),
        "ignoredReason": str(getattr(result, "ignored_reason", "") or ""),
    }


def memory_skill_generating_payload() -> dict[str, Any]:
    """生成 Chat 轻量 Skill 生成中提醒 payload，不包含 Skill 内容。"""

    return {
        "type": "memory",
        "message": "Skills 生成中",
    }
