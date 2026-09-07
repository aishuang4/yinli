from pathlib import Path
from types import SimpleNamespace

from src.agents.runtime_types import AgentGuardrails, AgentModelOptions, RuntimeToolScope
from src.server.routes.chat.models import ChatMessage
from src.server.routes.chat.plan_mode.manager import PlanModeManager
from src.server.routes.chat.plan_mode.middleware import PlanModeMiddleware
from src.server.routes.chat.plan_mode.tools import build_plan_mode_tools
from src.server.routes.chat.react_runtime import (
    _build_plan_mode_runtime_parts,
    _build_runtime_feature_parts,
)
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

    assert [tool.name for tool in tools] == [
        "plan_enter",
        "plan_write",
        "plan_set_steps",
        "plan_request_review",
        "task_list",
        "task_output",
        "plan_request_replan",
        "plan_complete",
    ]
    assert middleware.manager is manager


def test_react_runtime_constructs_plan_mode_without_approval_state() -> None:
    """Plan runtime parts 不从旧审批状态隐式恢复。"""

    ctx = type("Ctx", (), {
        "session_id": "session-1",
        "agent_id": "agent-1",
        "agent_name": "Agent",
        "persist_run": True,
    })()

    manager, cache_key, _tools, _middlewares = _build_plan_mode_runtime_parts(
        ctx,
        ChatMessage(message="请先制定计划"),
    )

    assert cache_key.startswith("plan-mode-v2:")
    assert manager.context.plan_active is False
    assert manager.context.markdown == ""


def test_react_runtime_only_enables_plan_features_for_explicit_plan_request(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.server.routes.chat.react_runtime.load_subagent_catalog",
        lambda workspace_dir: SimpleNamespace(definitions={}, fingerprint="none"),
    )
    ctx = SimpleNamespace(
        session_id="session-1",
        agent_id="agent-1",
        agent_name="Agent",
        persist_run=True,
        intent_route=SimpleNamespace(executor=SimpleNamespace(kind="single_agent")),
        runtime_context=SimpleNamespace(
            project_root=Path("/tmp/project"),
            workspace_id="default",
            project_id="default",
        ),
    )

    normal_parts = _build_runtime_feature_parts(ctx, ChatMessage(message="直接回答"))
    plan_parts = _build_runtime_feature_parts(
        ctx,
        ChatMessage(message="先制定计划", plan_requested=True),
    )

    assert normal_parts[0] is None
    assert "scheduled-job-tools:" in normal_parts[1]
    assert [tool.name for tool in normal_parts[2]] == [
        "ScheduleJob",
        "ListJobs",
        "PauseJob",
        "ConfirmJob",
        "CheckJobFeasibility",
        "RecheckJobCapabilities",
    ]
    assert plan_parts[0] is not None
    assert plan_parts[0].context.plan_active is True
    assert plan_parts[1].startswith("plan-mode-v2:")
    assert "scheduled-job-tools:" in plan_parts[1]
