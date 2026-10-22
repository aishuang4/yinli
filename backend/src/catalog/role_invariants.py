"""Job 与系统 Agent 的身份禁令。Markdown 编解码在 `profiles.py`。"""
from __future__ import annotations


from src.agents.system_agents import (
    is_default_chat_agent_id,
    is_default_websearch_job_agent_id,
)
from src.catalog.agent_models import AgentResponse, PlanConfig
from src.scheduler.job_toolkit import (
    CHAT_ROLE,
    JOB_ROLE,
    JobAgentError,
    bound_resource_ids,
    empty_job_toolkit,
    parse_agent_role,
    parse_job_toolkit,
    validate_job_agent_config,
)


def apply_role_invariants(agent: AgentResponse, warnings: list[str]) -> AgentResponse:
    """应用 job / 系统 Agent 身份禁令。失败抛 ValueError。"""

    if is_default_chat_agent_id(agent.id) and parse_agent_role(agent.role) == JOB_ROLE:
        raise ValueError("default 不能改为 job")
    if is_default_websearch_job_agent_id(agent.id) and parse_agent_role(agent.role) != JOB_ROLE:
        raise ValueError("system-websearch-job 不能改为 chat")

    role = parse_agent_role(agent.role)
    toolkit = parse_job_toolkit(agent.job_toolkit)
    guardrails = agent.guardrails
    mcp_tools = list(agent.mcp_tools)
    knowledge_tools = list(agent.knowledge_tools)
    skills = list(agent.skills)
    if role == JOB_ROLE:
        try:
            toolkit = validate_job_agent_config(
                agent_id=agent.id,
                role=role,
                toolkit=toolkit,
                guardrails=guardrails,
            )
        except JobAgentError as exc:
            raise ValueError(str(exc)) from exc
        bound = bound_resource_ids(toolkit)
        mcp_tools = list(bound["mcp"])
        knowledge_tools = list(dict.fromkeys([*bound["http"], *bound["knowledge"]]))
        skills = list(bound["skill"])
        if is_default_websearch_job_agent_id(agent.id):
            if not guardrails.network_enabled:
                warnings.append("system-websearch-job 必须保持网络开启")
                guardrails = guardrails.model_copy(update={"network_enabled": True})
            required_ids = {item.id for item in toolkit.required if item.kind == "builtin"}
            if not {"WebFetch", "HttpRequest"} <= required_ids:
                raise ValueError("system-websearch-job 必注入必须包含 WebFetch 与 HttpRequest")
    else:
        toolkit = empty_job_toolkit()
        if is_default_chat_agent_id(agent.id):
            role = CHAT_ROLE
    return agent.model_copy(update={
        "role": role,
        "job_toolkit": toolkit,
        "guardrails": guardrails,
        "mcp_tools": mcp_tools,
        "knowledge_tools": knowledge_tools,
        "skills": skills,
        "status": "idle",
        "plan_config": PlanConfig(),
    })
