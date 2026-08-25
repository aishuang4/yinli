"""定时 firing 无人值守工具裁剪常量。

交互式 Chat 仍注入 AskUser / Plan / DelegateTask；scheduled firing
只通过 denylist 与 feature key 隔离缓存，不改 Agent 全局 guardrails。
"""

from __future__ import annotations

import json
from typing import Any

from src.agents.runtime_types import RuntimeToolScope


SCHEDULED_JOB_UNATTENDED_DENYLIST: frozenset[str] = frozenset({
    "AskUser",
    "DelegateTask",
    "SubagentTaskList",
    "SubagentTaskWait",
    "SubagentResultRead",
    "SubagentTaskCancel",
    "plan_enter",
    "plan_write",
    "plan_set_steps",
    "plan_request_review",
    "task_list",
    "task_output",
    "plan_request_replan",
    "plan_complete",
    "ScheduleJob",
    "ListJobs",
    "DescribeJob",
    "PauseJob",
    "ResumeJob",
    "UpdateJob",
    "DeleteJob",
    "ConfirmJob",
})


def scheduled_job_unattended_feature_key(
    denylist: frozenset[str] | set[str] | None = None,
) -> str:
    """runtime_features_cache_key：与 scheduled-job-tools 区分，并含 denylist 指纹。"""

    names = denylist if denylist is not None else SCHEDULED_JOB_UNATTENDED_DENYLIST
    fingerprint = ",".join(sorted(names))
    return f"scheduled-job-unattended:{fingerprint}"


def deserialize_frozen_runtime_tool_scope(payload: Any) -> RuntimeToolScope:
    """从 Job JSON 反序列化冻结 ToolScope；缺省键视为 false，禁止全 True 默认。"""

    data: dict[str, Any]
    if payload is None:
        data = {}
    elif isinstance(payload, RuntimeToolScope):
        return RuntimeToolScope(
            allow_mcp=bool(payload.allow_mcp),
            allow_knowledge=bool(payload.allow_knowledge),
            allow_bash=bool(payload.allow_bash),
            allow_http=bool(payload.allow_http),
            allow_web=bool(payload.allow_web),
        )
    elif hasattr(payload, "model_dump"):
        data = dict(payload.model_dump())
    elif isinstance(payload, str):
        parsed = json.loads(payload)
        data = parsed if isinstance(parsed, dict) else {}
    elif isinstance(payload, dict):
        data = payload
    else:
        data = {}

    def flag(*keys: str) -> bool:
        for key in keys:
            if key in data:
                return bool(data[key])
        return False

    return RuntimeToolScope(
        allow_mcp=flag("allow_mcp", "allowMcp"),
        allow_knowledge=flag("allow_knowledge", "allowKnowledge"),
        allow_bash=flag("allow_bash", "allowBash"),
        allow_http=flag("allow_http", "allowHttp"),
        allow_web=flag("allow_web", "allowWeb"),
    )
