"""群聊工作室结束后的记忆收口。

close(confirm=True) 之后把置顶 Goal 与最近工人发言拼成一轮对话，
走 MemoryAdmissionGate → 历史节点 + MemoryReflectionAgent。

归属第一个 Worker 的 Agent，禁止 group-studio-host。
后台运行，失败只写 MEMORY_AUDIT，不影响 done。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from typing import Any

from src.group_studio.models import GROUP_STUDIO_HOST_AGENT_ID, GroupStudio
from src.storage.database import list_group_studio_messages

logger = logging.getLogger(__name__)

MAX_HUMAN_SNIPPETS = 3
MAX_WORKER_CHARS = 6000
MAX_MINUTES_CHARS = 6000


def build_archive_digest(studio: GroupStudio) -> tuple[str, str]:
    """返回 (用户侧文本, 工人结论文本)。"""

    pin = studio.pin
    title = studio.title or "未命名工作室"
    goal = (pin.goal or "").strip() or f"群聊《{title}》"
    messages = list_group_studio_messages(studio.id, limit=80)
    human_bits = [
        " ".join(str(item.get("body") or "").split())
        for item in messages
        if item.get("role") == "human" and str(item.get("body") or "").strip()
    ][-MAX_HUMAN_SNIPPETS:]
    user_parts = [goal]
    if pin.notes.strip():
        user_parts.append(f"备注：{pin.notes.strip()}")
    if human_bits:
        user_parts.append("最近人类消息：" + " / ".join(bit[:160] for bit in human_bits))
    goal_text = "\n".join(user_parts)

    worker_lines = [
        f"{item.get('speakerId') or '工人'}：{' '.join(str(item.get('body') or '').split())}"
        for item in messages
        if item.get("role") == "worker" and str(item.get("body") or "").strip()
    ]
    minutes = "\n".join(worker_lines) or f"《{title}》已结束，没有工人可见正文。"
    if len(minutes) > MAX_WORKER_CHARS:
        minutes = minutes[-MAX_WORKER_CHARS:].lstrip()
    if len(minutes) > MAX_MINUTES_CHARS:
        minutes = minutes[:MAX_MINUTES_CHARS].rstrip() + "\n…"
    return goal_text, minutes


def owner_agent_for_archive(studio: GroupStudio) -> tuple[str, str]:
    """归档记忆归属的 Agent：第一个 Worker；Host 永远不算。"""

    for member in studio.members:
        if member.role == "worker" and member.agent_id and member.agent_id != GROUP_STUDIO_HOST_AGENT_ID:
            return member.agent_id, member.display_name
    return "default", "Assistant"


def schedule_archive_memory(studio: GroupStudio) -> None:
    """结束后在后台跑记忆收口；没有运行中的事件循环时直接跳过。"""

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(run_archive_memory(studio), name=f"group-archive-memory-{studio.id}")


async def run_archive_memory(studio: GroupStudio) -> dict[str, Any]:
    """同步部分：准入 + 历史节点；后台部分：Markdown 提炼。"""

    from src.config.runtime_settings import get_effective_model_settings, model_binding_scope, resolve_model_binding
    from src.memory.node_persist import MemoryNodePersistInput, persist_completed_run_memory_node
    from src.memory.reflection import (
        MemoryReflectionInput,
        evaluate_turn_admission,
        reflect_completed_run_memory_with_agents,
    )
    from src.memory.trace import memory_reflection_audit_payload, record_memory_audit_event

    goal_text, minutes = build_archive_digest(studio)
    agent_id, agent_name = owner_agent_for_archive(studio)
    source_run_id = f"group:{studio.id}"
    item = MemoryReflectionInput(
        session_id=studio.chat_session_id,
        agent_id=agent_id,
        agent_name=agent_name,
        user_message=goal_text,
        assistant_text=minutes,
        workspace_id=studio.workspace_id,
        project_id=studio.project_id,
        source_run_id=source_run_id,
    )
    summary: dict[str, Any] = {"groupId": studio.id, "agentId": agent_id}
    try:
        admission = evaluate_turn_admission(item)
        node = persist_completed_run_memory_node(
            MemoryNodePersistInput(
                session_id=studio.chat_session_id,
                agent_id=agent_id,
                agent_name=agent_name,
                user_message=goal_text,
                assistant_text=minutes,
                workspace_id=studio.workspace_id,
                project_id=studio.project_id,
                source_run_id=source_run_id,
            ),
            admission=admission,
        )
        summary["nodeId"] = str((node or {}).get("id") or "")
        record_memory_audit_event(
            session_id=studio.chat_session_id,
            persist=True,
            agent_id=agent_id,
            agent_name=agent_name,
            action="group_archive_memory",
            status="completed" if node else "skipped",
            title="Group studio archived into memory" if node else "Group studio archive skipped by admission",
            content=(studio.pin.goal or studio.title)[:200],
            payload={**summary, "admissionReason": getattr(admission, "reason", "")},
        )
        if admission.sensitive or not admission.should_persist_history:
            return summary
        archive_admission = replace(admission, should_write_markdown=True)
        with model_binding_scope(resolve_model_binding(studio.model_config_id, validate=False)):
            model_name = get_effective_model_settings().default_model
            result = await reflect_completed_run_memory_with_agents(
                item, model_name=model_name, rule_admission=archive_admission
            )
        payload = memory_reflection_audit_payload(result)
        summary["markdownUpdateCount"] = payload.get("markdownUpdateCount")
        summary["markdownCandidateCount"] = payload.get("markdownCandidateCount")
        record_memory_audit_event(
            session_id=studio.chat_session_id,
            persist=True,
            agent_id=agent_id,
            agent_name=agent_name,
            action="group_archive_reflection",
            status="completed",
            title="Group studio archive reflected into Markdown memory",
            content=str(result.ignored_reason or ""),
            payload={**summary, **payload},
        )
    except Exception as exc:
        logger.warning("群聊工作室归档记忆收口失败: %s", exc, exc_info=True)
        record_memory_audit_event(
            session_id=studio.chat_session_id,
            persist=True,
            agent_id=agent_id,
            agent_name=agent_name,
            action="group_archive_memory",
            status="failed",
            title="Group studio archive memory failed",
            content=type(exc).__name__,
            payload=summary,
        )
    return summary
