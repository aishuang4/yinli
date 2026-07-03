import asyncio

from src.server.routes.agent_builder import (
    BuilderApplyRequest,
    BuilderPlanRequest,
    _heuristic_plan,
    _normalize_plan,
    apply_agent_team,
)
from src.server.routes.agents import AgentResponse, Guardrails


def test_agent_builder_agents_mode_does_not_generate_callable_subagent_refs():
    """Subagent 下线后，Agent Builder 不再生成 callableAgents 关系。"""

    request = BuilderPlanRequest(
        requirement="创建一个产品经理 Agent，需要能调用架构专家和数据分析专家。",
        mode="agents",
    )

    plan = _normalize_plan(request, _heuristic_plan(request))

    assert plan.mode == "agents"
    assert plan.team is None
    assert len(plan.agents) == 1
    assert all(agent.role == "worker" for agent in plan.agents)
    assert plan.agents[0].callable_agent_refs == []


def test_agent_builder_apply_does_not_write_callable_agents(monkeypatch):
    """Apply 草案时只创建 Agent，不再回写 callableAgents。"""

    request = BuilderPlanRequest(
        requirement="创建一个产品经理 Agent，需要能调用架构专家和数据分析专家。",
        mode="agents",
    )
    plan = _normalize_plan(request, _heuristic_plan(request))
    created_payloads = []

    async def fake_create_agent(data):
        agent_id = f"agent-{len(created_payloads) + 1}"
        created_payloads.append(data)
        return AgentResponse(
            id=agent_id,
            name=data.name,
            avatar="",
            description=data.description,
            system_prompt=data.system_prompt,
            modelConfig=data.model_config_data,
            mcpTools=data.mcp_tools,
            knowledgeTools=data.knowledge_tools,
            skills=data.skills,
            tags=data.tags,
            callableAgents=[],
            maxDepth=data.max_depth,
            planConfig=data.plan_config,
            guardrails=data.guardrails,
            status="idle",
            created_at="2026-06-23T00:00:00+00:00",
            updated_at="2026-06-23T00:00:00+00:00",
        )

    monkeypatch.setattr("src.server.routes.agent_builder.create_agent", fake_create_agent)

    response = asyncio.run(apply_agent_team(BuilderApplyRequest(plan=plan)))

    assert len(created_payloads) == 1
    assert response.created_team is None
    assert response.created_agents[0].callable_agents == []
