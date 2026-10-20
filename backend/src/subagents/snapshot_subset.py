"""把父 Run snapshot 收成 Subagent 明確可用的子集。

子集只保留定義點名、且已經凍結在父 snapshot 裡的資源。不讀取當前全局 grant。
"""

from __future__ import annotations

from typing import Any

from src.subagents.models import SubagentDefinition


def child_snapshot_subset(
    snapshot: dict[str, Any] | None,
    definition: SubagentDefinition,
) -> dict[str, Any] | None:
    """沒有父 snapshot 時返回 None，呼叫方維持既有父工具清單。"""

    if not snapshot:
        return None
    skill_ids = set(definition.skills)
    named_tools = set(definition.tools or [])
    allow_unnamed_tools = definition.tools is None
    items: list[dict[str, str]] = []
    for item in (snapshot.get("resources") or {}).get("items") or []:
        kind = str(item.get("resourceType") or "")
        identifier = str(item.get("resourceId") or "")
        if not identifier:
            continue
        if kind == "skill" and identifier in skill_ids:
            items.append({"resourceType": kind, "resourceId": identifier})
        elif kind == "mcp" and (allow_unnamed_tools or identifier in named_tools):
            items.append({"resourceType": kind, "resourceId": identifier})
        elif kind in {"http_tool", "knowledge"} and (allow_unnamed_tools or identifier in named_tools):
            items.append({"resourceType": kind, "resourceId": identifier})
    return {
        "id": str(snapshot.get("id") or ""),
        "sessionId": str(snapshot.get("sessionId") or ""),
        "resources": {"items": items},
    }


def resource_ids(snapshot: dict[str, Any] | None, *types: str) -> set[str]:
    items = (snapshot or {}).get("resources", {}).get("items") or []
    return {
        str(item.get("resourceId") or "")
        for item in items
        if item.get("resourceType") in types and item.get("resourceId")
    }
