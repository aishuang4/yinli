"""
Agent memory routes and storage helpers.

This is an application-level memory layer used by the workstation UI. It keeps
per-agent facts and episodic events in JSON files, independent from the
AgentScope runtime state.
"""

import json
import re
from pathlib import Path
from typing import Any, Literal
from datetime import datetime, timezone
import uuid

from fastapi import APIRouter
from pydantic import BaseModel, Field

from src.storage.database import load_agent_memory, save_agent_memory

router = APIRouter()

MEMORY_DIR = Path("data/agent_memories")
MEMORY_NAMESPACE = "agent_memories"

MemoryEventType = Literal[
    "user_message",
    "assistant_message",
    "skill_use",
    "tool_use",
    "team_message",
    "note",
]


class MemoryFact(BaseModel):
    """Long-term fact attached to an Agent."""

    id: str
    title: str
    content: str
    tags: list[str] = Field(default_factory=list)
    created_at: str
    updated_at: str


class MemoryEvent(BaseModel):
    """Episodic event attached to an Agent."""

    id: str
    type: MemoryEventType
    title: str
    content: str
    session_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class AgentMemory(BaseModel):
    """Persisted memory document for one Agent."""

    agent_id: str
    facts: list[MemoryFact] = Field(default_factory=list)
    events: list[MemoryEvent] = Field(default_factory=list)
    updated_at: str


class MemoryFactCreate(BaseModel):
    """Create/update a long-term memory fact."""

    title: str
    content: str
    tags: list[str] = Field(default_factory=list)


class MemoryCandidate(BaseModel):
    """Candidate long-term memory extracted after an Agent run."""

    title: str
    content: str
    tags: list[str] = Field(default_factory=list)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _memory_path(agent_id: str) -> Path:
    return MEMORY_DIR / f"{agent_id}.json"


def _load_memory(agent_id: str) -> AgentMemory:
    data = load_agent_memory(agent_id)
    if data:
        try:
            return AgentMemory.model_validate(data)
        except Exception:
            pass

    path = _memory_path(agent_id)
    if not path.exists():
        return AgentMemory(agent_id=agent_id, updated_at=_utc_now())

    try:
        memory = AgentMemory.model_validate(json.loads(path.read_text(encoding="utf-8")))
        save_agent_memory(memory.model_dump(mode="json"))
        return memory
    except Exception:
        return AgentMemory(agent_id=agent_id, updated_at=_utc_now())


def _save_memory(memory: AgentMemory) -> None:
    memory.updated_at = _utc_now()
    save_agent_memory(memory.model_dump(mode="json"))


def _migrate_existing_memory_files() -> None:
    """Import existing per-agent memory JSON files into SQLite."""
    if not MEMORY_DIR.exists():
        return

    for path in MEMORY_DIR.glob("*.json"):
        agent_id = path.stem
        if load_agent_memory(agent_id) is not None:
            continue
        try:
            memory = AgentMemory.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            continue
        save_agent_memory(memory.model_dump(mode="json"))


_migrate_existing_memory_files()


def append_agent_memory_event(
    *,
    agent_id: str | None,
    event_type: MemoryEventType,
    title: str,
    content: str,
    session_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Append an episodic memory event for an Agent."""
    if not agent_id or agent_id == "default":
        return

    memory = _load_memory(agent_id)
    memory.events.append(MemoryEvent(
        id=uuid.uuid4().hex,
        type=event_type,
        title=title[:120],
        content=content[:6000],
        session_id=session_id,
        metadata=metadata or {},
        created_at=_utc_now(),
    ))
    memory.events = memory.events[-500:]
    _save_memory(memory)


def maybe_summarize_agent_run(
    *,
    agent_id: str | None,
    agent_name: str,
    session_id: str,
    user_message: str,
    assistant_text: str,
    metadata: dict[str, Any] | None = None,
) -> list[MemoryFact]:
    """Post-run hook that promotes useful run-level learnings to facts.

    This is intentionally deterministic for now: it only writes memories when
    the run contains strong signals such as user preferences, stable project
    facts, or concrete successful tool/skill/team usage. It can be replaced by
    an LLM-based summarizer later without changing the chat lifecycle.
    """
    if not agent_id or agent_id == "default" or not assistant_text.strip():
        return []

    memory = _load_memory(agent_id)
    candidates = _extract_memory_candidates(
        agent_name=agent_name,
        user_message=user_message,
        assistant_text=assistant_text,
        metadata=metadata or {},
    )
    if not candidates:
        return []

    created: list[MemoryFact] = []
    existing_keys = {
        _normalize_memory_key(fact.title, fact.content)
        for fact in memory.facts
    }
    now = _utc_now()
    for candidate in candidates:
        key = _normalize_memory_key(candidate.title, candidate.content)
        if key in existing_keys:
            continue
        fact = MemoryFact(
            id=uuid.uuid4().hex,
            title=candidate.title[:120],
            content=candidate.content[:1200],
            tags=candidate.tags[:8],
            created_at=now,
            updated_at=now,
        )
        memory.facts.append(fact)
        existing_keys.add(key)
        created.append(fact)

    if not created:
        return []

    memory.facts = memory.facts[-200:]
    memory.events.append(MemoryEvent(
        id=uuid.uuid4().hex,
        type="note",
        title="自动记忆总结",
        content="\n".join(f"- {fact.title}: {fact.content}" for fact in created),
        session_id=session_id,
        metadata={
            "agentName": agent_name,
            "source": "post_run_memory_hook",
            "factIds": [fact.id for fact in created],
        },
        created_at=now,
    ))
    memory.events = memory.events[-500:]
    _save_memory(memory)
    return created


def _extract_memory_candidates(
    *,
    agent_name: str,
    user_message: str,
    assistant_text: str,
    metadata: dict[str, Any],
) -> list[MemoryCandidate]:
    """Extract conservative memory candidates from a completed run."""
    candidates: list[MemoryCandidate] = []
    user_text = " ".join(user_message.strip().split())
    assistant_summary = _compact_text(assistant_text, 700)

    preference_patterns = [
        r"(?:我希望|我需要|以后|下次|记住|偏好|不要|必须|统一|默认)([^。！？\n]{4,120})",
        r"(?:prefer|remember|next time|always|never|default)([^.\n]{4,160})",
    ]
    for pattern in preference_patterns:
        for match in re.finditer(pattern, user_text, flags=re.IGNORECASE):
            content = match.group(0).strip()
            if _is_actionable_memory(content):
                candidates.append(MemoryCandidate(
                    title="用户偏好",
                    content=content,
                    tags=["preference", "auto"],
                ))

    if any(keyword in user_text for keyword in ["项目", "后端", "前端", "sqlite", "Agent", "Team", "MCP", "Skills"]):
        if any(keyword in assistant_text for keyword in ["已", "完成", "实现", "修复", "迁移", "验证"]):
            candidates.append(MemoryCandidate(
                title="项目实现经验",
                content=f"用户需求：{_compact_text(user_text, 240)}\n处理结果：{assistant_summary}",
                tags=["project", "implementation", "auto"],
            ))

    skill_ids = metadata.get("skillIds") or []
    if skill_ids:
        candidates.append(MemoryCandidate(
            title="Skill 使用经验",
            content=f"{agent_name} 在本轮任务中成功使用 Skills: {', '.join(map(str, skill_ids))}。",
            tags=["skill", "auto"],
        ))

    team_name = metadata.get("teamName")
    if team_name:
        candidates.append(MemoryCandidate(
            title="Team 协作经验",
            content=f"{agent_name} 在 Team「{team_name}」中完成协作任务：{_compact_text(user_text, 220)}",
            tags=["team", "auto"],
        ))

    return candidates[:5]


def _is_actionable_memory(text: str) -> bool:
    """Filter out vague or too-short memories."""
    compact = _compact_text(text, 200)
    if len(compact) < 12:
        return False
    vague = ["帮我", "看看", "处理一下", "改一下"]
    return not any(compact == item for item in vague)


def _compact_text(text: str, limit: int) -> str:
    compact = " ".join(text.strip().split())
    if len(compact) <= limit:
        return compact
    return compact[:limit] + "..."


def _normalize_memory_key(title: str, content: str) -> str:
    return re.sub(r"\s+", "", f"{title}:{content}".lower())[:240]


@router.get("/{agent_id}", response_model=AgentMemory)
async def get_agent_memory(agent_id: str) -> AgentMemory:
    """Get memory for an Agent."""
    memory = _load_memory(agent_id)
    memory.events = sorted(memory.events, key=lambda item: item.created_at, reverse=True)
    memory.facts = sorted(memory.facts, key=lambda item: item.updated_at, reverse=True)
    return memory


@router.post("/{agent_id}/facts", response_model=MemoryFact)
async def create_memory_fact(agent_id: str, data: MemoryFactCreate) -> MemoryFact:
    """Create a long-term memory fact for an Agent."""
    now = _utc_now()
    fact = MemoryFact(
        id=uuid.uuid4().hex,
        title=data.title,
        content=data.content,
        tags=data.tags[:8],
        created_at=now,
        updated_at=now,
    )
    memory = _load_memory(agent_id)
    memory.facts.append(fact)
    _save_memory(memory)
    return fact


@router.delete("/{agent_id}/facts/{fact_id}", status_code=204)
async def delete_memory_fact(agent_id: str, fact_id: str) -> None:
    """Delete a long-term memory fact."""
    memory = _load_memory(agent_id)
    memory.facts = [fact for fact in memory.facts if fact.id != fact_id]
    _save_memory(memory)
