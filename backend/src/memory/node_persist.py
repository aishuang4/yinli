"""Agent 完成运行后的长期节点记忆持久化。"""

from __future__ import annotations

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
    source_run_id: str = ""
    source_message_id: str = ""


def persist_completed_run_memory_node(
    item: MemoryNodePersistInput,
    *,
    service: MemoryNodeService | None = None,
) -> dict | None:
    """把成功完成的单轮对话写入长期节点记忆。

    只保存用户输入、Agent 最终输出和规则摘要，不保存工具调用、observation 或 thinking。
    """

    user_message = item.user_message.strip()
    assistant_text = item.assistant_text.strip()
    admission = evaluate_memory_admission(
        MemoryAdmissionInput(
            user_message=user_message,
            assistant_text=assistant_text,
        )
    )
    if not admission.should_persist_history:
        return None
    node_date = date.today().isoformat()
    node = MemoryNodeWrite(
        agent_id=item.agent_id,
        node_date=node_date,
        title=_title_from_user_message(user_message),
        summary=_summary_from_turn(user_message, assistant_text),
        conversation_digest=_digest_from_turn(user_message, assistant_text),
        user_inputs=[user_message],
        agent_final_outputs=[assistant_text],
        tags=_tags_from_text(user_message + "\n" + assistant_text),
        source_session_id=item.session_id,
        source_run_id=item.source_run_id,
        source_message_id=item.source_message_id,
        confidence=0.7,
    )
    return (service or MemoryNodeService()).upsert_node(node)


def _title_from_user_message(value: str) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= 40:
        return normalized or "对话记忆"
    return normalized[:40].rstrip() + "..."


def _summary_from_turn(user_message: str, assistant_text: str) -> str:
    return _trim(
        "\n".join(
            [
                f"用户请求：{_trim(user_message, 280)}",
                f"Agent 输出：{_trim(assistant_text, 420)}",
            ],
        ),
        900,
    )


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
