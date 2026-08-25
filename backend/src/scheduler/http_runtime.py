"""HTTP / legacy knowledge 工具纯函数。

输入为 storage 记录，禁止 import `src.server.routes`。
"""

from __future__ import annotations

from typing import Any


def enabled_http_tools(
    agent_tool_ids: list[str],
    http_records: list[dict[str, Any]],
    *,
    allowed_ids: set[str] | frozenset[str] | None = None,
) -> list[dict[str, Any]]:
    """按 Agent 绑定 ∩ Project binding 过滤已启用 HTTP 工具。"""

    selected = {str(item) for item in agent_tool_ids if str(item).strip()}
    result: list[dict[str, Any]] = []
    for item in http_records:
        tool_id = str(item.get("id") or "")
        if not tool_id or tool_id not in selected:
            continue
        if allowed_ids is not None and tool_id not in allowed_ids:
            continue
        if not bool(item.get("enabled", True)):
            continue
        result.append(item)
    return result


def enabled_knowledge_tools(
    agent_tool_ids: list[str],
    knowledge_records: list[dict[str, Any]],
    occupied_http_ids: set[str] | frozenset[str],
    *,
    allowed_ids: set[str] | frozenset[str] | None = None,
) -> list[dict[str, Any]]:
    """返回尚未投影成 HTTP 工具的 legacy 知识库行。"""

    selected = {str(item) for item in agent_tool_ids if str(item).strip()}
    occupied = {str(item) for item in occupied_http_ids}
    result: list[dict[str, Any]] = []
    for item in knowledge_records:
        tool_id = str(item.get("id") or "")
        if not tool_id or tool_id not in selected or tool_id in occupied:
            continue
        if allowed_ids is not None and tool_id not in allowed_ids:
            continue
        if not bool(item.get("enabled", True)):
            continue
        result.append(item)
    return result
