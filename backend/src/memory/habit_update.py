"""习惯判断。

用户这句话落上时间线之后、智能体开口之前由系统叫醒。寒暄不调用模型。
"""

from __future__ import annotations

import json
import logging

from pydantic import BaseModel, Field

from src.memory.admission import has_habit_signal, is_trivial_user_message
from src.memory.json_agent import extract_json_object, run_json_agent, system_agent_id
from src.memory.markdown_store import MarkdownMemoryStore
from src.memory.prompt_cache import invalidate_habit_prompts
from src.memory.trace import record_memory_audit_event

logger = logging.getLogger(__name__)


class HabitUpdatePayload(BaseModel):
    """习惯智能体的结构化输出。"""

    should_write: bool = Field(default=False, alias="shouldWrite")
    content: str = ""
    reason: str = Field(default="", max_length=400)

    model_config = {"populate_by_name": True, "extra": "ignore"}


async def apply_habit_update(
    *,
    user_message: str,
    model_name: str,
    fallback_names: list[str] | None = None,
    user_id: str = "default",
    session_id: str = "",
    agent_id: str = "",
    agent_name: str = "",
    persist_audit: bool = True,
    store: MarkdownMemoryStore | None = None,
) -> str | None:
    """返回写入后的习惯全文；没改或失败时返回 None。"""

    text = " ".join(str(user_message or "").split())
    if is_trivial_user_message(text) or not has_habit_signal(text):
        return None
    memory_store = store or MarkdownMemoryStore()
    current = memory_store.read_habits(user_id).content.strip()
    try:
        payload = await run_habit_update_agent(
            user_message=text,
            habits=current,
            model_name=model_name,
            fallback_names=fallback_names or [],
        )
    except Exception as exc:
        logger.warning("习惯判断失败: %s", exc, exc_info=True)
        record_memory_audit_event(
            session_id=session_id,
            persist=persist_audit,
            agent_id=agent_id,
            agent_name=agent_name,
            action="habit_update",
            status="failed",
            title="Habit update failed",
            content=type(exc).__name__,
        )
        return None
    if payload is None or not payload.should_write or not payload.content.strip():
        record_memory_audit_event(
            session_id=session_id,
            persist=persist_audit,
            agent_id=agent_id,
            agent_name=agent_name,
            action="habit_update",
            status="skipped",
            title="Habit update skipped",
            content="" if payload is None else payload.reason,
        )
        return None
    saved = memory_store.write_habits(user_id, payload.content.strip())
    invalidate_habit_prompts()
    record_memory_audit_event(
        session_id=session_id,
        persist=persist_audit,
        agent_id=agent_id,
        agent_name=agent_name,
        action="habit_update",
        status="completed",
        title="Habit updated",
        content=payload.reason,
        payload={"chars": len(saved.content)},
    )
    return saved.content


async def run_habit_update_agent(
    *,
    user_message: str,
    habits: str,
    model_name: str,
    fallback_names: list[str] | None = None,
) -> HabitUpdatePayload | None:
    """判断这句用户话要不要改写这个人的习惯全文。"""

    text = await run_json_agent(
        agent_id=system_agent_id("habit-update", model_name, fallback_names or [], "2026-09-28-v1"),
        name="HabitUpdateAgent",
        system_prompt="\n".join([
            "你是习惯判断智能体。只返回一个 JSON 对象，不要写 markdown fence，也不要调用工具。",
            "习惯属于这个人，所有智能体会共用。只记录稳定偏好、语气和人设。",
            "不要记录单次任务步骤、临时路径或这一轮的具体结论。",
            "如果现有习惯已经覆盖这句话，或者这句话不是习惯，shouldWrite=false，content 置空。",
            "如果要改，content 必须是写入后的完整习惯全文，可以删掉过时句子，不要只返回增量。",
            'Schema: {"shouldWrite": false, "content": "", "reason": ""}',
        ]),
        user_prompt=json.dumps({"userMessage": user_message, "habits": habits}, ensure_ascii=False),
        model_name=model_name,
        fallback_names=fallback_names or [],
        max_tokens=1600,
    )
    data = extract_json_object(text)
    if not data:
        return None
    return HabitUpdatePayload.model_validate(data)
