"""时间线原文读取。

只负责按标签从聊天、定时任务和群聊表取出行，不决定注入或摄取。
"""

from __future__ import annotations

from typing import Any

from src.storage import database


def search_timeline_messages(
    *,
    workspace_id: str = "",
    project_id: str = "",
    agent_id: str = "",
    surface: str = "",
    time_from: str = "",
    time_to: str = "",
    speaker: str = "",
    query_text: str = "",
    limit: int = 12,
) -> list[dict[str, Any]]:
    """按标签和时间查询聊天、定时任务、群聊原文。"""

    database.ensure_db()
    cap = max(1, min(int(limit or 12), 40))
    rows: list[dict[str, Any]] = []
    with database._lock, database._connect() as conn:
        if surface in {"", "chat", "job"}:
            rows.extend(_search_chat(
                conn,
                workspace_id=workspace_id,
                project_id=project_id,
                agent_id=agent_id,
                surface=surface,
                time_from=time_from,
                time_to=time_to,
                speaker=speaker,
                query_text=query_text,
                limit=cap,
            ))
        if surface in {"", "group"}:
            rows.extend(_search_group(
                conn,
                workspace_id=workspace_id,
                project_id=project_id,
                agent_id=agent_id,
                time_from=time_from,
                time_to=time_to,
                speaker=speaker,
                query_text=query_text,
                limit=cap,
            ))
    rows.sort(key=lambda item: str(item.get("createdAt") or ""), reverse=True)
    return rows[:cap]


def list_timeline_neighbors(
    *,
    session_id: str = "",
    group_id: str = "",
    around_sort_order: int = 0,
    each_side: int = 20,
) -> list[dict[str, Any]]:
    """取出同一会话或群聊里命中点前后的原文。"""

    database.ensure_db()
    side = max(0, min(int(each_side or 0), 40))
    low = int(around_sort_order) - side
    high = int(around_sort_order) + side
    with database._lock, database._connect() as conn:
        if session_id:
            found = conn.execute(
                """
                SELECT m.id, m.session_id, m.role, m.content, m.agent_id, m.agent_name,
                       m.created_at, m.sort_order, s.workspace_id, s.project_id, s.kind
                FROM chat_messages m
                JOIN chat_sessions s ON s.id = m.session_id
                WHERE m.session_id = ? AND m.sort_order BETWEEN ? AND ?
                ORDER BY m.sort_order ASC
                """,
                (session_id, low, high),
            ).fetchall()
            return [_chat_message(row) for row in found]
        if group_id:
            found = conn.execute(
                """
                SELECT m.id, m.group_id, m.role, m.body, m.created_at, m.sort_order,
                       s.workspace_id, s.project_id, COALESCE(mb.agent_id, '') AS agent_id,
                       COALESCE(mb.display_name, '') AS agent_name
                FROM group_messages m
                JOIN group_studios s ON s.id = m.group_id
                LEFT JOIN group_memberships mb ON mb.id = m.speaker_id AND mb.group_id = m.group_id
                WHERE m.group_id = ? AND m.sort_order BETWEEN ? AND ? AND m.role != 'system'
                ORDER BY m.sort_order ASC
                """,
                (group_id, low, high),
            ).fetchall()
            return [_group_message(row) for row in found]
    return []


def _search_chat(conn, **filters) -> list[dict[str, Any]]:
    clauses, params = _shared_clauses(
        filters,
        workspace_column="s.workspace_id",
        project_column="s.project_id",
        time_column="m.created_at",
        text_column="m.content",
    )
    surface = filters["surface"]
    if surface == "chat":
        clauses.append("s.kind = 'chat'")
    elif surface == "job":
        clauses.append("s.kind = 'scheduled_job'")
    else:
        clauses.append("s.kind IN ('chat', 'scheduled_job')")
    if filters["agent_id"]:
        clauses.append("m.agent_id = ?")
        params.append(filters["agent_id"])
    if filters["speaker"] == "user":
        clauses.append("m.role = 'user'")
    elif filters["speaker"] == "assistant":
        clauses.append("m.role = 'assistant'")
    params.append(filters["limit"])
    found = conn.execute(
        f"""
        SELECT m.id, m.session_id, m.role, m.content, m.agent_id, m.agent_name,
               m.created_at, m.sort_order, s.workspace_id, s.project_id, s.kind
        FROM chat_messages m
        JOIN chat_sessions s ON s.id = m.session_id
        WHERE {' AND '.join(clauses)}
        ORDER BY m.created_at DESC
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [_chat_message(row) for row in found]


def _search_group(conn, **filters) -> list[dict[str, Any]]:
    clauses, params = _shared_clauses(
        filters,
        workspace_column="s.workspace_id",
        project_column="s.project_id",
        time_column="m.created_at",
        text_column="m.body",
    )
    clauses.append("m.role != 'system'")
    if filters["agent_id"]:
        clauses.append("COALESCE(mb.agent_id, '') = ?")
        params.append(filters["agent_id"])
    if filters["speaker"] == "user":
        clauses.append("m.role = 'human'")
    elif filters["speaker"] == "assistant":
        clauses.append("m.role != 'human'")
    params.append(filters["limit"])
    found = conn.execute(
        f"""
        SELECT m.id, m.group_id, m.role, m.body, m.created_at, m.sort_order,
               s.workspace_id, s.project_id, COALESCE(mb.agent_id, '') AS agent_id,
               COALESCE(mb.display_name, m.speaker_id, '') AS agent_name
        FROM group_messages m
        JOIN group_studios s ON s.id = m.group_id
        LEFT JOIN group_memberships mb ON mb.id = m.speaker_id AND mb.group_id = m.group_id
        WHERE {' AND '.join(clauses)}
        ORDER BY m.created_at DESC
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [_group_message(row) for row in found]


def _shared_clauses(filters: dict[str, Any], *, workspace_column: str, project_column: str, time_column: str, text_column: str) -> tuple[list[str], list[Any]]:
    clauses = ["1=1"]
    params: list[Any] = []
    if filters["workspace_id"]:
        clauses.append(f"{workspace_column} = ?")
        params.append(filters["workspace_id"])
    if filters["project_id"]:
        clauses.append(f"{project_column} = ?")
        params.append(filters["project_id"])
    if filters["time_from"]:
        clauses.append(f"{time_column} >= ?")
        params.append(filters["time_from"])
    if filters["time_to"]:
        clauses.append(f"{time_column} <= ?")
        params.append(filters["time_to"])
    if str(filters["query_text"] or "").strip():
        clauses.append(f"{text_column} LIKE ?")
        params.append(f"%{str(filters['query_text']).strip()}%")
    return clauses, params


def _chat_message(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "sessionId": row["session_id"],
        "groupId": "",
        "role": "user" if row["role"] == "user" else "assistant",
        "content": row["content"] or "",
        "agentId": row["agent_id"] or "",
        "agentName": row["agent_name"] or "",
        "createdAt": row["created_at"] or "",
        "sortOrder": int(row["sort_order"] or 0),
        "workspaceId": row["workspace_id"] or "",
        "projectId": row["project_id"] or "",
        "surface": "job" if row["kind"] == "scheduled_job" else "chat",
    }


def _group_message(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "sessionId": "",
        "groupId": row["group_id"],
        "role": "user" if row["role"] == "human" else "assistant",
        "content": row["body"] or "",
        "agentId": row["agent_id"] or "",
        "agentName": row["agent_name"] or "",
        "createdAt": row["created_at"] or "",
        "sortOrder": int(row["sort_order"] or 0),
        "workspaceId": row["workspace_id"] or "",
        "projectId": row["project_id"] or "",
        "surface": "group",
    }
