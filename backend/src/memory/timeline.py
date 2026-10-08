"""时间线查询。

节点就是已有的聊天、定时任务和群聊记录。这里只查询，不另写一份记忆。
"""

from __future__ import annotations

from src.storage.timeline_records import list_timeline_neighbors, search_timeline_messages

TIMELINE_HIT_LIMIT = 12
TIMELINE_NEIGHBOR_EACH_SIDE = 20
TIMELINE_MAX_CHARS = 12000


def search_timeline(
    *,
    workspace_id: str = "",
    project_id: str = "",
    agent_id: str = "",
    surface: str = "",
    time_from: str = "",
    time_to: str = "",
    speaker: str = "",
    query_text: str = "",
    limit: int = TIMELINE_HIT_LIMIT,
) -> dict:
    """按标签和时间找出原文，并带上同一会话里的邻近消息。"""

    hits = search_timeline_messages(
        workspace_id=workspace_id,
        project_id=project_id,
        agent_id=agent_id,
        surface=surface,
        time_from=time_from,
        time_to=time_to,
        speaker=speaker,
        query_text=query_text,
        limit=limit,
    )
    messages: list[dict] = []
    seen: set[str] = set()
    truncated = False
    used_chars = 0
    for hit in hits:
        window = list_timeline_neighbors(
            session_id=str(hit.get("sessionId") or ""),
            group_id=str(hit.get("groupId") or ""),
            around_sort_order=int(hit.get("sortOrder") or 0),
            each_side=TIMELINE_NEIGHBOR_EACH_SIDE,
        ) or [hit]
        for item in window:
            message_id = str(item.get("id") or "")
            if not message_id or message_id in seen:
                continue
            content = str(item.get("content") or "")
            if used_chars + len(content) > TIMELINE_MAX_CHARS:
                truncated = True
                break
            seen.add(message_id)
            messages.append(item)
            used_chars += len(content)
        if truncated:
            break
    messages.sort(key=lambda item: (str(item.get("createdAt") or ""), int(item.get("sortOrder") or 0)))
    return {"messages": messages, "truncated": truncated, "hitCount": len(hits)}
