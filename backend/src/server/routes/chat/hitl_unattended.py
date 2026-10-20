"""定时 Leader 无人值守 HITL overlay。

cwd 内 Write/Edit 自动批准，其余拒绝。不得写入 PENDING_INTERACTIONS，
也不得把 Team Member 的 deny-only 决策套到 scheduled Leader。
恢复事件形状与 Team 共享 `build_automatic_confirm_resume`。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agentscope.event import ExternalExecutionResultEvent, UserConfirmResultEvent

from src.server.routes.chat.hitl import build_automatic_confirm_resume
from src.tools.scoped_paths import resolve_workspace_path


MAX_UNATTENDED_TOOL_DENIALS = 8
UNATTENDED_AUTO_ALLOW_TOOLS = frozenset({"Write", "Edit"})
_UNATTENDED_DENIAL_MESSAGE = "定时任务无人值守，该工具已拒绝。"


def hitl_unattended_resolve(
    event_type: str,
    data: dict[str, Any],
    *,
    workspace_dir: str,
    approval_mode: str = "manual",
) -> tuple[list[str], UserConfirmResultEvent | ExternalExecutionResultEvent]:
    """在进入 waiting_* 之前决议 HITL。

    返回 (denied_tool_names, resume_event)。批准的 cwd Write/Edit 不计入 deny。
    """

    tool_calls = [
        dict(item)
        for item in (data.get("tool_calls") or [])
        if isinstance(item, dict)
    ]
    if event_type != "REQUIRE_USER_CONFIRM":
        denied = [str(item.get("name") or "tool") for item in tool_calls]
        resume = build_automatic_confirm_resume(
            event_type=event_type,
            reply_id=str(data.get("reply_id") or ""),
            tool_calls=tool_calls,
            default_confirmed=False,
            denial_message=_UNATTENDED_DENIAL_MESSAGE,
        )
        return denied, resume

    confirmed_by_id: dict[str, bool] = {}
    denied: list[str] = []
    for tool_call in tool_calls:
        tool_id = str(tool_call.get("id") or "")
        allowed = approval_mode != "manual" and is_unattended_cwd_write_or_edit(tool_call, workspace_dir)
        if tool_id:
            confirmed_by_id[tool_id] = allowed
        if not allowed:
            denied.append(str(tool_call.get("name") or "tool"))
    resume = build_automatic_confirm_resume(
        event_type=event_type,
        reply_id=str(data.get("reply_id") or ""),
        tool_calls=tool_calls,
        confirmed_by_id=confirmed_by_id,
        default_confirmed=False,
        denial_message=_UNATTENDED_DENIAL_MESSAGE,
    )
    return denied, resume


def is_unattended_cwd_write_or_edit(tool_call: dict[str, Any], workspace_dir: str) -> bool:
    """工具名是 Write/Edit 且规范化路径落在 firing cwd 内。"""

    name = str(tool_call.get("name") or "").strip()
    if name not in UNATTENDED_AUTO_ALLOW_TOOLS:
        return False
    cwd = str(workspace_dir or "").strip()
    if not cwd:
        return False
    file_path = _tool_call_file_path(tool_call)
    if not file_path:
        return False
    try:
        resolved = Path(resolve_workspace_path(
            file_path, base_dir=cwd, scope_dir=cwd,
        )).expanduser().resolve()
        return resolved.is_relative_to(Path(cwd).expanduser().resolve())
    except (ValueError, OSError):
        return False


def _tool_call_file_path(tool_call: dict[str, Any]) -> str:
    payload = tool_call.get("input")
    if isinstance(payload, str):
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            return ""
        payload = parsed if isinstance(parsed, dict) else {}
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("file_path") or payload.get("path") or "").strip()
