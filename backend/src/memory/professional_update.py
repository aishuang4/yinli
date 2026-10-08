"""专业区判断。

这一轮已经写进时间线之后后台运行，只影响下一轮注入。
"""

from __future__ import annotations

import asyncio
import json
import logging

from pydantic import BaseModel, Field

from src.memory.admission import is_trivial_turn
from src.memory.extraction_agents import _extract_json_object, _run_json_agent, _system_agent_id
from src.memory.markdown_store import MarkdownMemoryStore
from src.memory.prompt_cache import invalidate_professional_prompts
from src.memory.timeline import search_timeline
from src.memory.trace import record_memory_audit_event

logger = logging.getLogger(__name__)


class ProfessionalUpdatePayload(BaseModel):
    """专业区判断智能体的结构化输出。"""

    should_write: bool = Field(default=False, alias="shouldWrite")
    content: str = ""
    reason: str = Field(default="", max_length=400)

    model_config = {"populate_by_name": True, "extra": "ignore"}


def schedule_professional_update(
    *,
    agent_id: str,
    agent_name: str,
    user_message: str,
    assistant_text: str,
    model_name: str,
    fallback_names: list[str] | None = None,
    session_id: str = "",
    persist_audit: bool = True,
) -> None:
    """后台判断专业区要不要改写。没有事件循环时直接跳过。"""

    if not agent_id or not str(assistant_text or "").strip():
        return
    if is_trivial_turn(user_message, assistant_text):
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(
        apply_professional_update(
            agent_id=agent_id,
            agent_name=agent_name,
            user_message=user_message,
            assistant_text=assistant_text,
            model_name=model_name,
            fallback_names=fallback_names or [],
            session_id=session_id,
            persist_audit=persist_audit,
        ),
        name=f"professional-update-{agent_id}",
    )


async def apply_professional_update(
    *,
    agent_id: str,
    agent_name: str,
    user_message: str,
    assistant_text: str,
    model_name: str,
    fallback_names: list[str] | None = None,
    session_id: str = "",
    persist_audit: bool = True,
    store: MarkdownMemoryStore | None = None,
) -> str | None:
    """返回写入后的专业全文；不改或失败时返回 None。"""

    memory_store = store or MarkdownMemoryStore()
    current = memory_store.read_professional(agent_id).content.strip()
    recent = _recent_turns(agent_id)
    try:
        payload = await run_professional_update_agent(
            agent_id=agent_id,
            professional=current,
            user_message=user_message,
            assistant_text=assistant_text,
            recent_turns=recent,
            model_name=model_name,
            fallback_names=fallback_names or [],
        )
    except Exception as exc:
        logger.warning("专业区判断失败: %s", exc, exc_info=True)
        record_memory_audit_event(
            session_id=session_id,
            persist=persist_audit,
            agent_id=agent_id,
            agent_name=agent_name,
            action="professional_update",
            status="failed",
            title="Professional update failed",
            content=type(exc).__name__,
        )
        return None
    if payload is None or not payload.should_write:
        record_memory_audit_event(
            session_id=session_id,
            persist=persist_audit,
            agent_id=agent_id,
            agent_name=agent_name,
            action="professional_update",
            status="skipped",
            title="Professional update skipped",
            content="" if payload is None else payload.reason,
        )
        return None
    saved = memory_store.write_professional(agent_id, payload.content.strip())
    invalidate_professional_prompts(agent_id=agent_id)
    record_memory_audit_event(
        session_id=session_id,
        persist=persist_audit,
        agent_id=agent_id,
        agent_name=agent_name,
        action="professional_update",
        status="completed",
        title="Professional updated",
        content=payload.reason,
        payload={"chars": len(saved.content)},
    )
    return saved.content


def _recent_turns(agent_id: str) -> list[dict[str, str]]:
    try:
        payload = search_timeline(agent_id=agent_id, limit=8)
    except Exception:
        logger.debug("读取专业区近期时间线失败", exc_info=True)
        return []
    turns = []
    for item in list(payload.get("messages") or [])[-8:]:
        content = " ".join(str(item.get("content") or "").split())
        if not content:
            continue
        turns.append({
            "createdAt": str(item.get("createdAt") or ""),
            "role": str(item.get("role") or ""),
            "content": content[:500],
        })
    return turns


async def run_professional_update_agent(
    *,
    agent_id: str,
    professional: str,
    user_message: str,
    assistant_text: str,
    recent_turns: list[dict[str, str]],
    model_name: str,
    fallback_names: list[str] | None = None,
) -> ProfessionalUpdatePayload | None:
    """判断这一轮要不要改写该智能体的专业全文。"""

    text = await _run_json_agent(
        agent_id=_system_agent_id(
            "professional-update",
            model_name,
            fallback_names or [],
            f"2026-09-28-v1:{agent_id}",
        ),
        name="ProfessionalUpdateAgent",
        system_prompt="\n".join([
            "你是专业区判断智能体。只返回一个 JSON 对象，不要写 markdown fence，也不要调用工具。",
            "专业区属于这一个智能体，记录它反复做过、已经可以稳定复述的做法。",
            "不要把某一轮的冗长原文抄进去。要先对照 recentTurns，确认是同一件事又出现了，才改写。",
            "多数轮次应该 shouldWrite=false。",
            "如果要改，content 必须是完整新全文，可以比旧文短，用来删除不再重复的事项。",
            'Schema: {"shouldWrite": false, "content": "", "reason": ""}',
        ]),
        user_prompt=json.dumps(
            {
                "agentId": agent_id,
                "professional": professional,
                "userMessage": user_message,
                "assistantText": assistant_text,
                "recentTurns": recent_turns,
            },
            ensure_ascii=False,
        ),
        model_name=model_name,
        fallback_names=fallback_names or [],
        max_tokens=2200,
    )
    data = _extract_json_object(text)
    if not data:
        return None
    return ProfessionalUpdatePayload.model_validate(data)
