"""群聊工作室关闭时不再另压一份记忆。

进行中的人类发言走习惯判断，工人说完走专业区判断。时间线就是群聊消息本身。
"""
from __future__ import annotations


from typing import Any

from src.group_studio.models import GROUP_STUDIO_HOST_AGENT_ID, GroupStudio
from src.storage.records.group_studio_records import list_group_studio_messages

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
    """群聊进行中的发言已经各自进入时间线和专业区，关闭房间不再另压一份记忆。"""

    del studio
    return


async def run_archive_memory(studio: GroupStudio) -> dict[str, Any]:
    """关闭房间不再把整段对话压成一个历史节点。"""

    return {"groupId": studio.id, "skipped": "timeline_is_source", "agentId": owner_agent_for_archive(studio)[0]}
