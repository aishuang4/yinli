from src.agents.runtime_types import AgentGuardrails, AgentModelOptions, RuntimeToolScope
from src.server.routes.chat.plan_mode.manager import PlanModeManager
from src.server.routes.chat.plan_mode.middleware import PlanModeMiddleware
from src.server.routes.chat.plan_mode.tools import build_plan_mode_tools
from src.server.routes.chat.runtime_builder import RuntimeModelConfig, build_main_agent_runtime_spec


def test_plan_mode_cache_key_can_be_included_in_runtime_agent_id() -> None:
    spec = build_main_agent_runtime_spec(
        agent_id="agent-1",
        agent_name="Agent",
        session_id="session-1",
        system_prompt="prompt",
        model_config=RuntimeModelConfig(
            model_name="model-a",
            fallback_names=[],
            model_options=AgentModelOptions(),
        ),
        deep_thinking=False,
        used_skills=[],
        guardrails=AgentGuardrails(),
        mcp_servers=[],
        knowledge_tools=[],
        runtime_tool_scope=RuntimeToolScope(),
        guardrail_cache_key="guard",
        runtime_features_cache_key="plan-mode-v1",
        extra_tools=[],
        middlewares=[],
    )

    assert "features-plan-mode-v1" in spec.runtime_agent_id
    assert spec.extra_tools == []
    assert spec.middlewares == []


def test_plan_mode_tools_and_middleware_are_constructed_together() -> None:
    manager = PlanModeManager(
        session_id="session-1",
        run_id="run-1",
        agent_id="agent-1",
        agent_name="Agent",
        persist=False,
    )
    tools = build_plan_mode_tools(manager)
    middleware = PlanModeMiddleware(manager)

    assert [tool.name for tool in tools] == ["plan_enter", "plan_write", "plan_exit"]
    assert middleware.manager is manager
