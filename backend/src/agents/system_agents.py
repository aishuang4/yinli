"""系统内置 Agent 的稳定身份定义。"""

from __future__ import annotations

DEFAULT_CHAT_AGENT_ID = "default"
DEFAULT_WEBSEARCH_JOB_AGENT_ID = "system-websearch-job"
SYSTEM_AGENT_IDS: frozenset[str] = frozenset({
    DEFAULT_CHAT_AGENT_ID,
    DEFAULT_WEBSEARCH_JOB_AGENT_ID,
})


def is_system_agent_id(agent_id: str) -> bool:
    """判断 Agent 是否为跨 Workspace 可用的系统内置资源。"""

    return str(agent_id or "") in SYSTEM_AGENT_IDS


def is_default_chat_agent_id(agent_id: str) -> bool:
    """判断是否为系统默认 Chat Agent。"""

    return str(agent_id or "") == DEFAULT_CHAT_AGENT_ID


def is_default_websearch_job_agent_id(agent_id: str) -> bool:
    """判断是否为系统默认 WebSearch Job Agent。"""

    return str(agent_id or "") == DEFAULT_WEBSEARCH_JOB_AGENT_ID
