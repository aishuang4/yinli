"""Memory 路由共用的响应和审计。"""

from __future__ import annotations

from typing import Any

from src.memory.trace import record_memory_audit_event

def _markdown_doc_response(doc) -> dict[str, Any]:
    """统一转换 Markdown 记忆响应。"""

    return {
        "scope": doc.scope,
        "ownerId": doc.owner_id,
        "path": doc.path,
        "content": doc.content,
        "exists": doc.exists,
    }


def _record_memory_management_audit(
    *,
    session_id: str,
    agent_id: str,
    action: str,
    status: str,
    title: str,
    content: str,
    payload: dict[str, Any],
) -> None:
    record_memory_audit_event(
        session_id=session_id or "memory-page",
        persist=True,
        agent_id=agent_id or "",
        agent_name=agent_id or "Memory Page",
        action=action,
        status=status,
        title=title,
        content=content,
        payload=payload,
    )

