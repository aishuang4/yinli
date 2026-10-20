"""定时任务能力检查；需要人工批准属于执行策略，不能冒充能力缺失。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.scheduler.models import JobToolScope
from src.security.permissions import build_permission_profile


_ALWAYS_INJECTED_TOOLS = ("Read", "Grep", "Glob", "Write", "Edit")
_SCOPE_TOOL_NAMES = {
    "allow_bash": "Bash",
    "allow_http": "HttpRequest",
    "allow_web": "WebFetch",
    "allow_mcp": "MCP",
    "allow_knowledge": "Knowledge",
}


def skill_package_has_scripts(skill: Any | None) -> bool:
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


def frozen_skills_require_bash(skill_ids: list[str], *, project_root=None, workspace_id="default") -> bool:
    """任一冻结 Skill 含 scripts/ 则 confirm 时强制 allow_bash。"""

    from src.skills.registry import get_installed_skill

    return any(
        skill_package_has_scripts(get_installed_skill(skill_id, project_root=project_root, workspace_id=workspace_id))
        for skill_id in skill_ids
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
    extra_tool_names: list[str] | tuple[str, ...] = (),
) -> list[str]:
    """只阻止能力硬边界；审核可能需人工不等于能力不可用。"""

    profile = build_permission_profile(getattr(agent, "guardrails", None))
    injected = [*injected_unattended_tools(tool_scope), *extra_tool_names]
    if profile.is_read_only:
        return list(dict.fromkeys(name for name in injected if name not in {"Read", "Grep", "Glob"}))
    return []
