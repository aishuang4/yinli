"""Agent 完成运行后的长期节点记忆持久化。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from src.memory.admission import MemoryAdmissionInput, evaluate_memory_admission
from src.memory.nodes import MemoryNodeService, MemoryNodeWrite


@dataclass(frozen=True)
class MemoryNodePersistInput:
    """节点持久化输入。"""

    session_id: str
    agent_id: str
    agent_name: str
    user_message: str
    assistant_text: str
    project_slug: str = "default"
    source_run_id: str = ""
    source_message_id: str = ""


# 过期归档每个进程每天只跑一次，避免每轮都扫表。
_last_stale_sweep_date: str = ""


def persist_completed_run_memory_node(
    item: MemoryNodePersistInput,
    *,
    service: MemoryNodeService | None = None,
    admission: object | None = None,
) -> dict | None:
    """把成功完成的单轮对话写入长期节点记忆。

    只保存用户输入、Agent 最终输出和规则摘要，不保存工具调用、observation 或 thinking。
    同一会话同一天的多轮并进同一个节点；`admission` 可传入本轮已算好的准入结果，
    避免与 Markdown 提炼各评一次、口径不一致。
    """

    user_message = item.user_message.strip()
    assistant_text = item.assistant_text.strip()
    decision = admission or evaluate_memory_admission(
        MemoryAdmissionInput(
            user_message=user_message,
            assistant_text=assistant_text,
        )
    )
    if not getattr(decision, "should_persist_history", False):
        return None
    node_date = date.today().isoformat()
    turn_summary = _summary_from_turn(user_message, assistant_text)
    turn_digest = _digest_from_turn(user_message, assistant_text)
    node = MemoryNodeWrite(
        agent_id=item.agent_id,
        node_date=node_date,
        title=_title_from_user_message(user_message),
        summary=turn_summary,
        conversation_digest=turn_digest,
        user_inputs=[user_message],
        agent_final_outputs=[assistant_text],
        tags=_tags_from_text(user_message + "\n" + assistant_text),
        project_slug=item.project_slug,
        source_session_id=item.session_id,
        source_run_id=item.source_run_id,
        source_message_id=item.source_message_id,
        confidence=0.7,
    )
    node_service = service or MemoryNodeService()
    merged = node_service.append_turn_to_session_day_node(node, turn_summary=turn_summary, turn_digest=turn_digest)
    result = merged or node_service.upsert_node(node)
    _sweep_stale_nodes_once_per_day(node_service, today=node_date)
    return result


def _sweep_stale_nodes_once_per_day(service: MemoryNodeService, *, today: str) -> None:
    global _last_stale_sweep_date
    if _last_stale_sweep_date == today:
        return
    _last_stale_sweep_date = today
    try:
        service.archive_stale_nodes()
    except Exception:
        # 归档只是整理，不能影响本轮持久化。
        return


def _title_from_user_message(value: str) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= 40:
        return normalized or "对话记忆"
    return normalized[:40].rstrip() + "..."


_MARKDOWN_NOISE = re.compile(r"(```.*?```|`[^`]*`|^\s{0,3}#{1,6}\s*|^\s*[-*+]\s+|^\s*\d+\.\s+|\*\*|__|[|>])", re.DOTALL | re.MULTILINE)
_SENTENCE_END = re.compile(r"(?<=[。！？!?；;])\s*|(?<=[.])\s+")


def _first_sentence(value: str, limit: int) -> str:
    """去掉 Markdown 噪音后取第一句；没有句号就按长度裁。"""

    text = _MARKDOWN_NOISE.sub(" ", value or "")
    text = " ".join(text.split())
    if not text:
        return ""
    for piece in _SENTENCE_END.split(text):
        piece = piece.strip()
        # 跳过"好的""已完成"这类过短的开场白。
        if len(piece) >= 6:
            return _trim(piece, limit)
    return _trim(text, limit)


def _summary_from_turn(user_message: str, assistant_text: str) -> str:
    """一句话摘要：请求要点 + 结果要点，控制在约 160 字，供检索命中时快速判断相关性。

    完整原文仍保存在 `user_inputs` / `agent_final_outputs`；摘要不再复制大段输出，
    否则注入索引和向量文本都会被同一段话撑大。
    """

    request = _first_sentence(user_message, 60)
    result = _first_sentence(assistant_text, 100)
    if request and result:
        return _trim(f"{request} → {result}", 170)
    return _trim(request or result or "对话记忆", 170)


def _digest_from_turn(user_message: str, assistant_text: str) -> str:
    return _trim(
        f"本轮用户提出：{_trim(user_message, 180)}；Agent 最终回复：{_trim(assistant_text, 260)}",
        600,
    )


def _tags_from_text(value: str) -> list[str]:
    lowered = value.lower()
    tags = []
    for keyword, tag in [
        ("memory", "memory"),
        ("记忆", "memory"),
        ("zvec", "zvec"),
        ("hook", "hooks"),
        ("hooks", "hooks"),
        ("skill", "skills"),
        ("agent", "agent"),
    ]:
        if keyword in lowered and tag not in tags:
            tags.append(tag)
    return tags[:6]


def _trim(value: str, limit: int) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit].rstrip() + "..."
