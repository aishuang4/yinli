"""每轮注入最近可见群消息，避免主持人只看黑板误判未交付。"""

from __future__ import annotations

from typing import Any

RECENT_VISIBLE_LIMIT = 8
MESSAGE_SNIPPET_CHARS = 400


def recent_visible_messages(
    snapshot: dict[str, Any],
    *,
    limit: int = RECENT_VISIBLE_LIMIT,
) -> list[dict[str, Any]]:
    """时间线可见消息，旧→新，最多 `limit` 条。"""

    messages = [
        item
        for item in (snapshot.get("messages") or [])
        if isinstance(item, dict) and item.get("visibleInTimeline")
    ]
    return messages[-limit:]


def format_turn_context(snapshot: dict[str, Any]) -> str:
    """拼进 Host/Worker 本轮 user 消息：lastSpokeAt + 最近可见群消息。"""

    members = [
        item
        for item in (snapshot.get("members") or [])
        if isinstance(item, dict) and item.get("status") != "removed"
    ]
    names = {
        str(item.get("id") or ""): str(item.get("displayName") or item.get("role") or "成员")
        for item in members
    }
    lines = ["成员最近发言（空=从未写入 lastSpokeAt，不能单独当成未响应）："]
    if not members:
        lines.append("- （无成员）")
    else:
        for item in members:
            spoke = str(item.get("lastSpokeAt") or "").strip() or "从未"
            muted = "，已静音" if item.get("muted") else ""
            lines.append(
                f"- {item.get('displayName')} ({item.get('role')}) lastSpokeAt={spoke}{muted}"
            )
    lines.append(
        "最近群消息（旧→新；判断是否已交付以这里为准，黑板 artifacts 为空不等于没交稿）："
    )
    recent = recent_visible_messages(snapshot)
    if not recent:
        lines.append("- （暂无）")
    else:
        for item in recent:
            speaker_id = str(item.get("speakerId") or "")
            name = names.get(speaker_id) or (
                "人类" if str(item.get("role") or "") == "human" else speaker_id or "未知"
            )
            snippet = _snippet(str(item.get("body") or ""))
            role = str(item.get("role") or "")
            intent = str(item.get("intent") or "")
            lines.append(f"- {name}（{role}/{intent}）：{snippet}")
    return "\n".join(lines)


def _snippet(body: str) -> str:
    text = " ".join(body.split())
    if not text:
        return "（空）"
    if len(text) <= MESSAGE_SNIPPET_CHARS:
        return text
    return text[: MESSAGE_SNIPPET_CHARS - 1] + "…"


def note_member_spoke(group_id: str, speaker_id: str, *, role: str, body: str) -> None:
    """可见正文落库后回写 lastSpokeAt。"""

    if role not in {"host", "worker"}:
        return
    if not str(body or "").strip():
        return
    if not speaker_id or speaker_id == "human":
        return
    from src.storage.database import set_group_studio_member_last_spoke

    set_group_studio_member_last_spoke(group_id, speaker_id)
