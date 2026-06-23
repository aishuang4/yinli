import asyncio

from src.server.routes.agent_builder import (
    BuilderApplyRequest,
    BuilderPlanRequest,
    _heuristic_plan,
    _normalize_plan,
    apply_agent_team,
)
from src.server.routes.agents import AgentResponse, Guardrails


def test_agent_builder_agents_mode_generates_callable_subagent_refs():
    """Agent Builder 在 agents 模式下能生成主 Agent + 专家子 Agent 关系。"""

    request = BuilderPlanRequest(
        requirement="创建一个产品经理 Agent，需要能调用架构专家和数据分析专家。",
        mode="agents",
    )

    plan = _normalize_plan(request, _heuristic_plan(request))

    assert plan.mode == "agents"
    assert plan.team is None
    assert len(plan.agents) == 3
    assert all(agent.role == "worker" for agent in plan.agents)
    assert plan.agents[0].callable_agent_refs == [
        plan.agents[1].temp_id,
        plan.agents[2].temp_id,
    ]


def test_agent_builder_apply_maps_temp_refs_to_created_agent_ids(monkeypatch):
    """Apply 草案时先创建 Agent，再把草案引用回写成真实 callableAgents。"""

    request = BuilderPlanRequest(
        requirement="创建一个产品经理 Agent，需要能调用架构专家和数据分析专家。",
        mode="agents",
    )
    plan = _normalize_plan(request, _heuristic_plan(request))
    created_payloads = []
    update_payloads = []

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
            memoryEnabled=data.memory_enabled,
            planConfig=data.plan_config,
            guardrails=data.guardrails,
            status="idle",
            created_at="2026-06-23T00:00:00+00:00",
            updated_at="2026-06-23T00:00:00+00:00",
        )

    async def fake_update_agent(agent_id, data):
        update_payloads.append((agent_id, data.callable_agents))
        return AgentResponse(
            id=agent_id,
            name=f"updated-{agent_id}",
            avatar="",
            description="",
            system_prompt="You are a helpful assistant.",
            modelConfig=plan.agents[0].model_config_data,
            mcpTools=[],
            knowledgeTools=[],
            skills=[],
            tags=[],
            callableAgents=data.callable_agents or [],
            maxDepth=2,
            memoryEnabled=True,
            planConfig=plan.agents[0].plan_config,
            guardrails=Guardrails(human_approval=True),
            status="idle",
            created_at="2026-06-23T00:00:00+00:00",
            updated_at="2026-06-23T00:00:00+00:00",
        )

    monkeypatch.setattr("src.server.routes.agent_builder.create_agent", fake_create_agent)
    monkeypatch.setattr("src.server.routes.agent_builder.update_agent", fake_update_agent)

    response = asyncio.run(apply_agent_team(BuilderApplyRequest(plan=plan)))

    assert len(created_payloads) == 3
    assert response.created_team is None
    assert update_payloads == [("agent-1", ["agent-2", "agent-3"])]
    assert response.created_agents[0].callable_agents == ["agent-2", "agent-3"]
