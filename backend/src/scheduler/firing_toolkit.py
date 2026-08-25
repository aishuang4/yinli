"""到期 toolkit 装配。PR 1 仅提供空合同 = 今日五旗语义。"""

from __future__ import annotations

from typing import Any

from src.scheduler.capability_contract import parse_capability_contract
from src.scheduler.models import DEFAULT_JOB_TOOL_SCOPE, JobToolScope, ScheduledJob


def assemble_scheduled_firing_toolkit_spec(job: ScheduledJob | Any) -> dict[str, Any]:
    """空合同沿用 Job 列五旗（保留 allow_bash，忽略脏外呼旗）。"""

    contract = parse_capability_contract(getattr(job, "capability_contract", None))
    scope = getattr(job, "tool_scope", None)
    if not isinstance(scope, JobToolScope):
        scope = JobToolScope.model_validate(scope or {})
    if contract.is_generate_only() or contract.verdict == "generate_only":
        return {
            "tool_scope": JobToolScope(
                allow_bash=bool(scope.allow_bash),
                allow_http=False,
                allow_web=False,
                allow_mcp=False,
                allow_knowledge=False,
            ).model_dump(),
            "enable_tools": [],
            "denylist_only": True,
            "legacy_tool_scope_ignored": bool(
                scope.allow_mcp or scope.allow_http or scope.allow_web or scope.allow_knowledge
            ),
        }
    return {
        "tool_scope": dict(DEFAULT_JOB_TOOL_SCOPE),
        "enable_tools": [],
        "denylist_only": True,
        "placeholder": True,
    }
