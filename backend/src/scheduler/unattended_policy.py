"""无人值守 Job 的 confirm 工具检查。

Skill 含 scripts/ 视为需要 Bash；cwd Write/Edit 由 overlay 批准，
不计入 would_fail_tools。read_only 不能激活写产物 Job。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.scheduler.models import JobToolScope
from src.security.permissions import ApprovalPolicy, normalize_approval_policy
from src.server.routes.skills import Skill, get_installed_skill


_ALWAYS_INJECTED_TOOLS = ("Read", "Grep", "Glob", "Write", "Edit")
_OVERLAY_APPROVED_TOOLS = frozenset({"Write", "Edit"})
_SCOPE_TOOL_NAMES = {
    "allow_bash": "Bash",
    "allow_http": "HttpRequest",
    "allow_web": "WebFetch",
    "allow_mcp": "MCP",
    "allow_knowledge": "Knowledge",
}


def skill_package_has_scripts(skill: Skill | None) -> bool:
    """Skill 包存在 scripts/ 目录则视为需要 Bash。"""

    if skill is None:
        return False
    resource_path = str(getattr(skill, "resource_path", "") or "").strip()
    if resource_path:
        scripts_dir = Path(resource_path).expanduser() / "scripts"
        if scripts_dir.is_dir():
            return True
    for item in list(getattr(skill, "resources", None) or []):
        normalized = str(item or "").replace("\\", "/").lstrip("./")
        if normalized == "scripts" or normalized.startswith("scripts/"):
            return True
    return False


def frozen_skills_require_bash(skill_ids: list[str]) -> bool:
    """任一冻结 Skill 含 scripts/ 则 confirm 时强制 allow_bash。"""

    return any(
        skill_package_has_scripts(get_installed_skill(skill_id))
        for skill_id in skill_ids
    )


def agent_approval_policy(agent: object | None) -> ApprovalPolicy:
    """读取 Agent 用户可见权限策略。"""

    guardrails = getattr(agent, "guardrails", None)
    return normalize_approval_policy(
        getattr(guardrails, "tool_permission_profile", None),
        getattr(guardrails, "tool_permission_mode", None),
    )


def injected_unattended_tools(tool_scope: JobToolScope | Any) -> list[str]:
    """冻结 scope 为 true 的工具 ∪ {Read, Grep, Glob, Write, Edit}。"""

    names = list(_ALWAYS_INJECTED_TOOLS)
    for field, tool_name in _SCOPE_TOOL_NAMES.items():
        if bool(getattr(tool_scope, field, False)):
            names.append(tool_name)
    return names


def would_fail_unattended_tools(
    *,
    tool_scope: JobToolScope | Any,
    agent: object | None,
) -> list[str]:
    """Agent 策略下会 HITL 且 overlay 不会批准的注入工具。"""

    injected = injected_unattended_tools(tool_scope)
    policy = agent_approval_policy(agent)
    if policy == ApprovalPolicy.READ_ONLY:
        return [name for name in injected if name in _OVERLAY_APPROVED_TOOLS or name in _SCOPE_TOOL_NAMES.values()]
    if policy == ApprovalPolicy.TRUSTED_TOOLS:
        return []
    failing: list[str] = []
    for name in injected:
        if name in _OVERLAY_APPROVED_TOOLS:
            continue
        if name in {"Read", "Grep", "Glob"}:
            continue
        failing.append(name)
    return failing
