"""群聊工作室归档后的记忆收口。

一场工作室可能几十轮，Worker 每轮发言不走 Chat `completion.py`，此前归档后什么都不沉淀。
这里在 `close_studio(confirm=True)` 归档成功后，把**人类可见的结论**——任务卡 Goal / 背景 /
约束、黑板 decisions、工作项完成情况、DoD 勾选——拼成一份"会议纪要"，当作一轮完成对话
走同一条记忆链路：`MemoryAdmissionGate` → 历史节点 + 统一 `MemoryReflectionAgent` 提炼。

约束：
- 归属 Agent 用第一个 Worker（即发起工作室的 Chat Agent），禁止用 `group-studio-host` 写 agent memory。
- 只用结论，不用逐条群消息或 Host 的控场发言。
- 后台运行，失败只写 `MEMORY_AUDIT`，不影响归档。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from typing import Any

from src.group_studio.models import GROUP_STUDIO_HOST_AGENT_ID, GroupStudio

logger = logging.getLogger(__name__)

# 纪要各部分的条数 / 字符上限，避免几十轮黑板把一次提炼撑爆。
MAX_DECISIONS = 30
MAX_DECISION_CHARS = 240
MAX_WORK_ITEMS = 20
MAX_MINUTES_CHARS = 6000


def build_archive_digest(studio: GroupStudio) -> tuple[str, str]:
    """返回 (用户目标文本, 会议纪要文本)，分别扮演一轮对话里的用户消息与 Agent 最终回答。"""

    card = studio.task_card
    goal_parts = [f"群聊工作室目标：{card.goal}".strip()]
    if card.background:
        goal_parts.append(f"背景：{card.background}")
    if card.constraints:
        goal_parts.append("约束：" + "；".join(str(item) for item in card.constraints))
    goal_text = "\n".join(goal_parts)

    lines: list[str] = [f"《{studio.title}》工作室已归档，结论如下。"]
    decisions = [item for item in studio.blackboard.decisions][-MAX_DECISIONS:]
    if decisions:
        lines.append("已定结论：")
        for item in decisions:
            text = " ".join(str(item.text or "").split())
            lines.append(f"- {text[:MAX_DECISION_CHARS]}")
    resolved = [item for item in studio.blackboard.open_questions if item.resolved and item.resolution]
    if resolved:
        lines.append("已解决的问题：")
        for item in resolved[-10:]:
            lines.append(f"- {item.text[:120]} → {item.resolution[:160]}")
    done_items = [item for item in studio.work_items if str(item.status) == "done"][-MAX_WORK_ITEMS:]
    if done_items:
        lines.append("完成的工作项：")
        for item in done_items:
            lines.append(f"- {item.title[:120]}")
    if card.dod_checklist:
        checked = sum(1 for item in card.dod_checklist if item.checked)
        lines.append(f"完成定义：{checked}/{len(card.dod_checklist)} 项已勾选。")
        for item in card.dod_checklist:
            lines.append(f"- [{'x' if item.checked else ' '}] {item.text[:120]}")
    if studio.blackboard.artifacts:
        lines.append(f"产物：{len(studio.blackboard.artifacts)} 个。")
    workers = [member.display_name for member in studio.members if member.role == "worker"]
    if workers:
        lines.append("参与成员：" + "、".join(workers[:8]))
    minutes = "\n".join(lines)
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
    """归档后在后台跑记忆收口；没有运行中的事件循环时直接跳过。"""

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(run_archive_memory(studio), name=f"group-archive-memory-{studio.id}")


async def run_archive_memory(studio: GroupStudio) -> dict[str, Any]:
    """同步部分：准入 + 历史节点；后台部分：Markdown 提炼。返回审计摘要。"""

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
            content=studio.task_card.goal[:200],
            payload={**summary, "admissionReason": getattr(admission, "reason", "")},
        )
        if admission.sensitive or not admission.should_persist_history:
            return summary
        # 归档是人类确认过的结论（DoD 勾齐 + 确认关闭），黑板 decisions 本身就是"长期约定"的来源；
        # 规则闸门只看用户那句目标里有没有"以后 / 默认"之类的词，这里不适用，放行到 Markdown 提炼
        # （user / global 层仍会进待确认队列，不会静默写入）。Skill 进化仍按规则闸门。
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
