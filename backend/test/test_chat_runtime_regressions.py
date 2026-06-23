import asyncio
from types import SimpleNamespace

from src.server.routes.chat.background_runs import (
    ChatBackgroundRun,
    _broadcast,
    relay_chat_background_run_events,
    start_chat_background_run,
)
from src.server.routes.chat.handlers.send import _is_plan_runtime_tool_name
from src.server.routes.chat.helpers import _sse_event
from src.server.routes.chat.intent_router import (
    filter_team_members_for_route,
    refine_team_route_with_model,
    route_chat_message,
    route_trace_payload,
)
from src.server.routes.chat.models import ChatTeamMemberPayload, ChatToolRun
from src.server.routes.chat.runtime import _build_runtime_subagents, _permission_mode_for_guardrails
from src.server.routes.chat.runtime_builder import (
    RuntimeModelConfig,
    build_main_agent_runtime_spec,
    build_plan_runtime_context,
    build_runtime_agent_cache_key,
    build_runtime_model_config,
    create_main_agent_from_spec,
)
from src.server.routes.chat.team_state import _pending_tool_calls_to_runs
from src.server.routes.chat.trace_recorder import _model_cache_from_event, _permission_decision_payload
from src.server.routes.traces import _event_trace_status, _trace_status
from src.agents.factory import AgentGuardrails, AgentModelOptions, RuntimeToolScope, _build_context_config
from src.security.permissions import (
    PermissionAction,
    PermissionDecision,
    ResourceType,
    build_permission_profile,
)
from src.tools.ask_user import AskUser
from src.tools.plan_runtime import CompletePlanStep
from src.server.routes.chat.plans import create_model_first_runtime_plan, create_runtime_plan, mark_plan_step_for_agent, should_create_plan
from src.storage.database import get_plan_record


def test_model_cache_from_event_ignores_non_cache_usage():
    cache = _model_cache_from_event(
        {
            "id": "evt-1",
            "input_tokens": 100,
            "output_tokens": 20,
            "cache_creation_input_tokens": 0,
            "cache_input_tokens": 0,
            "created_at": "2026-06-18T00:00:00",
        },
        "deepseek-chat",
    )

    assert cache is None


def test_context_compression_schema_allows_project_discovery_summaries():
    context_config = _build_context_config()

    schema = context_config.summary_schema

    assert schema["properties"]["important_discoveries"]["maxLength"] >= 1000
    assert schema["properties"]["next_steps"]["maxLength"] >= 1000
    assert "under the schema maxLength" in context_config.compression_prompt


def test_model_cache_from_event_maps_provider_cache_usage():
    cache = _model_cache_from_event(
        {
            "id": "evt-2",
            "input_tokens": 100,
            "output_tokens": 20,
            "cache_creation_input_tokens": 32,
            "cache_input_tokens": 50,
            "created_at": "2026-06-18T00:00:00",
        },
        "deepseek-chat",
    )

    assert cache is not None
    assert cache.model_name == "deepseek-chat"
    assert cache.cache_creation_input_tokens == 32
    assert cache.cache_input_tokens == 50
    assert cache.cache_hit_rate == 50 / 150


def test_ask_user_is_external_tool_for_agentscope_resume():
    tool = AskUser()

    assert tool.name == "AskUser"
    assert tool.is_external_tool is True
    assert tool.input_schema["required"] == ["title", "questions"]


def test_complete_plan_step_updates_structured_plan_and_markdown():
    plan = create_runtime_plan(
        session_id="test-plan-session",
        agent_id="test-agent",
        team_id=None,
        title="测试计划",
        goal="验证 Plan 工具更新步骤",
        mode="structured",
        max_steps=3,
        team_members=[],
        has_tools=True,
        has_skills=False,
    )

    asyncio.run(CompletePlanStep(plan["id"])(step_index=0, result="第一步完成", activate_next=True))

    updated = get_plan_record(plan["id"])

    assert updated is not None
    assert updated["steps"][0]["status"] == "completed"
    assert updated["steps"][0]["result"] == "第一步完成"
    assert updated["steps"][1]["status"] == "active"
    assert "第一步完成" in updated["markdown"]


def test_plan_runtime_tool_name_detection_handles_prefixed_names():
    assert _is_plan_runtime_tool_name("CompletePlanStep")
    assert _is_plan_runtime_tool_name("agent_CompletePlanStep")
    assert _is_plan_runtime_tool_name("plan-runtime-update_plan_step")
    assert not _is_plan_runtime_tool_name("KnowledgeBaseSearch")


def test_plan_runtime_does_not_trigger_for_conversation_with_tools():
    agent_config = SimpleNamespace(plan_config=SimpleNamespace(enabled=True))

    assert not should_create_plan(
        agent_config=agent_config,
        message="你好，今天我们先简单聊聊这个项目背景。",
        has_team=False,
        has_tools=True,
        has_skills=False,
        has_images=False,
    )


def test_plan_runtime_triggers_for_explicit_planning_intent():
    agent_config = SimpleNamespace(plan_config=SimpleNamespace(enabled=True))

    assert should_create_plan(
        agent_config=agent_config,
        message="请先帮我规划一下这个功能的实现步骤，然后等我确认。",
        has_team=False,
        has_tools=False,
        has_skills=False,
        has_images=False,
    )


def test_plan_runtime_triggers_for_tool_execution_intent():
    agent_config = SimpleNamespace(plan_config=SimpleNamespace(enabled=True))

    assert should_create_plan(
        agent_config=agent_config,
        message="请检查后端聊天接口代码并修复计划模式每轮误触发的问题。",
        has_team=False,
        has_tools=True,
        has_skills=False,
        has_images=False,
    )


def test_plan_runtime_respects_disabled_config():
    agent_config = SimpleNamespace(plan_config=SimpleNamespace(enabled=False))

    assert not should_create_plan(
        agent_config=agent_config,
        message="请规划一下实现步骤。",
        has_team=True,
        has_tools=True,
        has_skills=True,
        has_images=True,
    )


def test_runtime_plan_uses_task_specific_steps_for_code_work():
    plan = create_runtime_plan(
        session_id="test-plan-code-session",
        agent_id="test-agent",
        team_id=None,
        title="修复计划模式",
        goal="请检查后端聊天接口代码并修复计划模式每轮误触发的问题。",
        mode="structured",
        max_steps=6,
        team_members=[],
        has_tools=True,
        has_skills=False,
    )

    titles = [step["title"] for step in plan["steps"]]

    assert titles == ["定位相关代码和约束", "实施必要改动", "运行验证", "汇总变更与结果"]
    assert "理解任务和约束" not in titles


def test_model_first_plan_uses_valid_model_steps(monkeypatch):
    async def fake_generate_plan_json_with_model(**kwargs):
        return {
            "title": "模型计划",
            "steps": [
                {"title": "读取现状", "description": "先确认已有实现", "assignedAgentId": "test-agent"},
                {"title": "执行改造", "description": "按需求修改", "assignedAgentId": "test-agent"},
            ],
        }

    monkeypatch.setattr(
        "src.server.routes.chat.plans._generate_plan_json_with_model",
        fake_generate_plan_json_with_model,
    )

    plan = asyncio.run(create_model_first_runtime_plan(
        session_id="test-model-plan-session",
        agent_id="test-agent",
        team_id=None,
        title="规则标题",
        goal="请实现模型优先计划。",
        mode="structured",
        max_steps=6,
        team_members=[],
        has_tools=True,
        has_skills=False,
        model_name="test-model",
        fallback_names=[],
        model_options=AgentModelOptions(),
    ))

    assert plan["title"] == "模型计划"
    assert [step["title"] for step in plan["steps"]] == ["读取现状", "执行改造"]


def test_model_first_plan_falls_back_to_rule_steps(monkeypatch):
    async def fake_generate_plan_json_with_model(**kwargs):
        return {"title": "坏计划", "steps": []}

    monkeypatch.setattr(
        "src.server.routes.chat.plans._generate_plan_json_with_model",
        fake_generate_plan_json_with_model,
    )

    plan = asyncio.run(create_model_first_runtime_plan(
        session_id="test-model-plan-fallback-session",
        agent_id="test-agent",
        team_id=None,
        title="兜底计划",
        goal="请检查后端聊天接口代码并修复计划模式问题。",
        mode="structured",
        max_steps=6,
        team_members=[],
        has_tools=True,
        has_skills=False,
        model_name="test-model",
        fallback_names=[],
        model_options=AgentModelOptions(),
    ))

    assert [step["title"] for step in plan["steps"]] == ["定位相关代码和约束", "实施必要改动", "运行验证", "汇总变更与结果"]


def test_rule_fallback_uses_task_dimensions_instead_of_fixed_code_template():
    plan = create_runtime_plan(
        session_id="test-plan-dimension-fallback-session",
        agent_id="test-agent",
        team_id=None,
        title="HTTP 工具管理调研",
        goal=(
            "分析 agent-workstation 里 HTTP 工具管理功能还缺哪些完善点。"
            "计划里需要包含前端、后端、测试、用户体验四个维度。"
        ),
        mode="structured",
        max_steps=6,
        team_members=[],
        has_tools=True,
        has_skills=False,
    )

    titles = [step["title"] for step in plan["steps"]]

    assert titles == [
        "界定调研目标和现状范围",
        "前端入口与交互",
        "后端接口与存储",
        "测试与回归验证",
        "用户体验与可用性",
        "收敛改进建议和优先级",
    ]
    assert "定位相关代码和约束" not in titles


def test_team_worker_completion_updates_assigned_plan_step():
    members = [
        SimpleNamespace(
            agent_id="worker-a",
            name="调研 Worker",
            role="worker",
            handoff_prompt="调研 HTTP 工具需求。",
            description="调研",
        ),
        SimpleNamespace(
            agent_id="leader",
            name="Leader",
            role="leader",
            handoff_prompt="",
            description="",
        ),
    ]
    plan = create_runtime_plan(
        session_id="test-team-plan-session",
        agent_id="leader",
        team_id="team-1",
        title="Team Plan",
        goal="团队协作完成 HTTP 工具调研。",
        mode="structured",
        max_steps=6,
        team_members=members,
        has_tools=True,
        has_skills=False,
    )

    updated = mark_plan_step_for_agent(
        plan,
        assigned_agent_id="worker-a",
        status="completed",
        result="调研 Worker 已完成。",
    )

    worker_step = next(step for step in updated["steps"] if step["assignedAgentId"] == "worker-a")
    assert worker_step["status"] == "completed"
    assert worker_step["result"] == "调研 Worker 已完成。"
    assert "调研 Worker 已完成。" in updated["markdown"]


def test_intent_route_selects_matching_subagent(monkeypatch):
    parent = SimpleNamespace(
        callable_agents=["research-agent", "code-agent"],
        plan_config=SimpleNamespace(enabled=True),
        mcp_tools=[],
        knowledge_tools=[],
    )
    children = {
        "research-agent": SimpleNamespace(
            id="research-agent",
            name="Research Agent",
            description="负责市场调研、资料搜索和竞品分析。",
            system_prompt="",
        ),
        "code-agent": SimpleNamespace(
            id="code-agent",
            name="Code Agent",
            description="负责代码实现、修复和测试。",
            system_prompt="",
        ),
    }

    monkeypatch.setattr(
        "src.server.routes.agents.get_registered_agent",
        lambda agent_id: children.get(agent_id),
    )

    route = route_chat_message(
        message="请交给 Research Agent 做竞品分析。",
        agent_config=parent,
        skill_ids=[],
        team_members=[],
        images=[],
    )

    assert route.mode == "subagent_execution"
    assert route.selected_subagent_ids == ["research-agent"]


def test_runtime_subagents_respect_empty_route_selection():
    parent = SimpleNamespace(
        id="parent-agent",
        callable_agents=["research-agent"],
        max_depth=2,
    )

    assert _build_runtime_subagents(parent, selected_subagent_ids=[]) == []


def test_runtime_subagents_warn_when_configured_child_is_missing(monkeypatch):
    parent = SimpleNamespace(
        id="parent-agent",
        name="Parent Agent",
        callable_agents=["missing-agent"],
        max_depth=2,
    )
    warnings = []
    monkeypatch.setattr(
        "src.server.routes.agents.get_registered_agent",
        lambda agent_id: None,
    )

    specs = _build_runtime_subagents(parent, on_warning=warnings.append)

    assert specs == []
    assert warnings[0]["eventType"] == "SUBAGENT_CALL_FAILED"
    assert warnings[0]["reason"] == "missing_subagent"
    assert warnings[0]["parentAgentId"] == "parent-agent"
    assert warnings[0]["subagentId"] == "missing-agent"


def test_intent_route_trace_payload_uses_api_aliases():
    route = route_chat_message(
        message="你好，简单聊聊。",
        agent_config=SimpleNamespace(
            callable_agents=[],
            plan_config=SimpleNamespace(enabled=True),
            mcp_tools=[],
            knowledge_tools=[],
        ),
        skill_ids=[],
        team_members=[],
        images=[],
    )

    payload = route_trace_payload(route)

    assert payload["mode"] == "conversation"
    assert payload["shouldCreatePlan"] is False
    assert payload["selectedSkillIds"] == []
    assert payload["selectedSubagentIds"] == []


def test_intent_route_clarification_blocks_plan_creation():
    route = route_chat_message(
        message="请修复一下。",
        agent_config=SimpleNamespace(
            callable_agents=[],
            plan_config=SimpleNamespace(enabled=True),
            mcp_tools=[],
            knowledge_tools=[],
        ),
        skill_ids=[],
        team_members=[],
        images=[],
    )

    assert route.mode == "clarification"
    assert route.require_clarification is True
    assert route.should_create_plan is False
    assert route.clarification["title"] == "需要补充目标对象"


def test_filter_team_members_for_route_keeps_leader_and_selected_workers():
    members = [
        ChatTeamMemberPayload(
            id="member-leader",
            agentId="leader-agent",
            name="Leader",
            role="leader",
            description="",
            executionMode="serial",
            dependsOn=[],
            handoffPrompt="",
        ),
        ChatTeamMemberPayload(
            id="member-research",
            agentId="research-agent",
            name="Research",
            role="worker",
            description="",
            executionMode="parallel",
            dependsOn=[],
            handoffPrompt="",
        ),
        ChatTeamMemberPayload(
            id="member-code",
            agentId="code-agent",
            name="Code",
            role="worker",
            description="",
            executionMode="parallel",
            dependsOn=[],
            handoffPrompt="",
        ),
    ]

    routed = filter_team_members_for_route(
        members,
        selected_member_ids=["research-agent"],
        leader_agent_id="leader-agent",
    )

    assert [member.agent_id for member in routed] == ["leader-agent", "research-agent"]


def test_team_route_model_refinement_updates_selected_workers(monkeypatch):
    async def fake_select_team_workers_for_route(**kwargs):
        return ["research-agent"], "模型选择 Research worker。"

    monkeypatch.setattr(
        "src.server.routes.chat.intent_router.select_team_workers_for_route",
        fake_select_team_workers_for_route,
    )
    route = route_chat_message(
        message="请让团队处理这个需求。",
        agent_config=SimpleNamespace(
            callable_agents=[],
            plan_config=SimpleNamespace(enabled=True),
            mcp_tools=[],
            knowledge_tools=[],
        ),
        skill_ids=[],
        team_members=[
            ChatTeamMemberPayload(
                id="member-leader",
                agentId="leader-agent",
                name="Leader",
                role="leader",
                description="",
                executionMode="serial",
                dependsOn=[],
                handoffPrompt="",
            ),
            ChatTeamMemberPayload(
                id="member-research",
                agentId="research-agent",
                name="Research",
                role="worker",
                description="负责资料调研。",
                executionMode="parallel",
                dependsOn=[],
                handoffPrompt="",
            ),
        ],
        images=[],
    )

    refined = asyncio.run(refine_team_route_with_model(
        route,
        message="请让团队处理这个需求。",
        agent_id="leader-agent",
        team_name="Demo Team",
        team_members=[
            ChatTeamMemberPayload(
                id="member-leader",
                agentId="leader-agent",
                name="Leader",
                role="leader",
                description="",
                executionMode="serial",
                dependsOn=[],
                handoffPrompt="",
            ),
            ChatTeamMemberPayload(
                id="member-research",
                agentId="research-agent",
                name="Research",
                role="worker",
                description="负责资料调研。",
                executionMode="parallel",
                dependsOn=[],
                handoffPrompt="",
            ),
        ],
        model_name="test-model",
    ))

    assert refined.mode == "team_execution"
    assert refined.selected_team_member_ids == ["research-agent"]
    team_decision = next(decision for decision in refined.decisions if decision.name == "team")
    assert team_decision.payload["selector"] == "model"


def test_intent_route_tool_scope_stays_minimal_for_conversation():
    route = route_chat_message(
        message="你好，解释一下这个概念。",
        agent_config=SimpleNamespace(
            callable_agents=[],
            plan_config=SimpleNamespace(enabled=True),
            mcp_tools=["mcp-1"],
            knowledge_tools=["kb-1"],
        ),
        skill_ids=[],
        team_members=[],
        images=[],
    )

    assert route.tool_scope.allow_mcp is False
    assert route.tool_scope.allow_knowledge is False
    assert route.tool_scope.allow_bash is False
    assert route.tool_scope.allow_http is False


def test_intent_route_tool_scope_enables_execution_and_research_tools():
    route = route_chat_message(
        message="请运行 pytest 并搜索知识库资料分析失败原因。",
        agent_config=SimpleNamespace(
            callable_agents=[],
            plan_config=SimpleNamespace(enabled=True),
            mcp_tools=["mcp-1"],
            knowledge_tools=["kb-1"],
        ),
        skill_ids=[],
        team_members=[],
        images=[],
    )

    assert route.tool_scope.allow_bash is True
    assert route.tool_scope.allow_http is True
    assert route.tool_scope.allow_knowledge is True


def test_runtime_tool_scope_cache_key_is_stable():
    scope = RuntimeToolScope(
        allow_mcp=False,
        allow_knowledge=True,
        allow_bash=False,
        allow_http=True,
    )

    assert scope.cache_key() == "mcp-0:knowledge-1:bash-0:http-1"


def test_runtime_builder_model_config_prefers_request_model():
    config = build_runtime_model_config(
        run_data=SimpleNamespace(model="request-model"),
        agent_config=SimpleNamespace(
            model_config_data=SimpleNamespace(
                primary="agent-model",
                fallback=["fallback-a"],
                temperature=0.2,
                top_p=0.9,
                max_tokens=1024,
                max_react_iters=12,
            ),
        ),
        runtime_model_settings=SimpleNamespace(
            default_model="runtime-model",
            fallback_models=["runtime-fallback"],
        ),
    )

    assert config.model_name == "request-model"
    assert config.fallback_names == ["fallback-a"]
    assert config.model_options.max_react_iters == 12


def test_runtime_builder_cache_key_includes_tool_scope_and_plan():
    key = build_runtime_agent_cache_key(
        agent_id="agent",
        session_id="session",
        model_name="model",
        deep_thinking=False,
        fallback_names=["fallback"],
        used_skills=[],
        mcp_servers=[],
        knowledge_tools=[],
        subagents=[],
        subagent_context=SimpleNamespace(max_depth=2),
        runtime_tool_scope=RuntimeToolScope(allow_mcp=False, allow_knowledge=False, allow_bash=True, allow_http=False),
        runtime_plan={"id": "plan-1"},
        guardrail_cache_key="guardrails",
    )

    assert "tools-mcp-0:knowledge-0:bash-1:http-0" in key
    assert "plan-plan-1" in key


def test_plan_runtime_builder_skips_when_route_does_not_require_plan():
    route = route_chat_message(
        message="你好，简单聊聊。",
        agent_config=SimpleNamespace(
            callable_agents=[],
            plan_config=SimpleNamespace(enabled=True),
            mcp_tools=[],
            knowledge_tools=[],
        ),
        skill_ids=[],
        team_members=[],
        images=[],
    )

    result = asyncio.run(build_plan_runtime_context(
        intent_route=route,
        agent_config=SimpleNamespace(plan_config=SimpleNamespace(mode="structured", max_steps=3)),
        session_id="session-no-plan",
        agent_id="agent",
        team_id=None,
        title="No plan",
        goal="你好，简单聊聊。",
        team_members=[],
        has_tools=False,
        has_skills=False,
        system_prompt="base prompt",
        model_config=RuntimeModelConfig(
            model_name="test-model",
            fallback_names=[],
            model_options=AgentModelOptions(),
        ),
    ))

    assert result.runtime_plan is None
    assert result.system_prompt == "base prompt"


def test_plan_runtime_builder_creates_plan_and_appends_prompt(monkeypatch):
    async def fake_create_model_first_runtime_plan(**kwargs):
        return create_runtime_plan(
            session_id=kwargs["session_id"],
            agent_id=kwargs["agent_id"],
            team_id=kwargs["team_id"],
            title=kwargs["title"],
            goal=kwargs["goal"],
            mode=kwargs["mode"],
            max_steps=kwargs["max_steps"],
            team_members=kwargs["team_members"],
            has_tools=kwargs["has_tools"],
            has_skills=kwargs["has_skills"],
        )

    monkeypatch.setattr(
        "src.server.routes.chat.runtime_builder.create_model_first_runtime_plan",
        fake_create_model_first_runtime_plan,
    )

    route = route_chat_message(
        message="请规划一下实现步骤。",
        agent_config=SimpleNamespace(
            callable_agents=[],
            plan_config=SimpleNamespace(enabled=True),
            mcp_tools=[],
            knowledge_tools=[],
        ),
        skill_ids=[],
        team_members=[],
        images=[],
    )

    result = asyncio.run(build_plan_runtime_context(
        intent_route=route,
        agent_config=SimpleNamespace(plan_config=SimpleNamespace(mode="structured", max_steps=3)),
        session_id="session-with-plan",
        agent_id="agent",
        team_id=None,
        title="计划",
        goal="请规划一下实现步骤。",
        team_members=[],
        has_tools=True,
        has_skills=False,
        system_prompt="base prompt",
        model_config=RuntimeModelConfig(
            model_name="test-model",
            fallback_names=[],
            model_options=AgentModelOptions(),
        ),
    ))

    assert result.runtime_plan is not None
    assert result.runtime_plan["goal"] == "请规划一下实现步骤。"
    assert "base prompt" in result.system_prompt
    assert "Plan Runtime" in result.system_prompt


def test_main_agent_runtime_spec_wraps_factory_inputs():
    tool_scope = RuntimeToolScope(allow_mcp=False, allow_knowledge=False, allow_bash=True, allow_http=False)
    spec = build_main_agent_runtime_spec(
        agent_id="agent",
        agent_name="Agent",
        session_id="session",
        system_prompt="prompt",
        model_config=RuntimeModelConfig(
            model_name="model",
            fallback_names=["fallback"],
            model_options=AgentModelOptions(max_react_iters=9),
        ),
        deep_thinking=True,
        used_skills=[],
        guardrails=AgentGuardrails(),
        mcp_servers=[],
        knowledge_tools=[],
        subagents=[],
        subagent_context=SimpleNamespace(max_depth=2),
        runtime_tool_scope=tool_scope,
        runtime_plan={"id": "plan-1"},
        guardrail_cache_key="guardrails",
    )

    assert spec.name == "Agent"
    assert spec.parent_agent_id == "agent"
    assert spec.plan_id == "plan-1"
    assert spec.tool_scope is tool_scope
    assert "thinking-True" in spec.runtime_agent_id
    assert "plan-plan-1" in spec.runtime_agent_id


def test_create_main_agent_from_spec_delegates_to_factory(monkeypatch):
    calls = {}

    def fake_get_or_create_agent(**kwargs):
        calls.update(kwargs)
        return "agent-instance"

    monkeypatch.setattr(
        "src.server.routes.chat.runtime_builder.get_or_create_agent",
        fake_get_or_create_agent,
    )
    tool_scope = RuntimeToolScope(allow_mcp=False, allow_knowledge=False, allow_bash=True, allow_http=False)
    spec = build_main_agent_runtime_spec(
        agent_id="agent",
        agent_name="Agent",
        session_id="session",
        system_prompt="prompt",
        model_config=RuntimeModelConfig(
            model_name="model",
            fallback_names=["fallback"],
            model_options=AgentModelOptions(max_react_iters=9),
        ),
        deep_thinking=False,
        used_skills=[],
        guardrails=AgentGuardrails(),
        mcp_servers=[],
        knowledge_tools=[],
        subagents=[],
        subagent_context=SimpleNamespace(max_depth=2),
        runtime_tool_scope=tool_scope,
        runtime_plan={"id": "plan-1"},
        guardrail_cache_key="guardrails",
    )

    result = create_main_agent_from_spec(
        spec,
        on_fallback_event=lambda event: None,
        on_subagent_event=lambda event: None,
    )

    assert result == "agent-instance"
    assert calls["agent_id"] == spec.runtime_agent_id
    assert calls["name"] == "Agent"
    assert calls["model_name"] == "model"
    assert calls["plan_id"] == "plan-1"
    assert calls["tool_scope"] is tool_scope
    assert callable(calls["on_fallback_event"])
    assert callable(calls["on_subagent_event"])


def test_unified_permission_profile_maps_legacy_bypass_to_trusted_tools():
    guardrails = AgentGuardrails(
        tool_permission_mode="bypass",
        tool_permission_profile="",
    )

    profile = build_permission_profile(guardrails)

    assert profile.tool_policy == "trusted_tools"
    assert profile.decide(ResourceType.TOOL, PermissionAction.READ) == PermissionDecision.ALLOW


def test_unified_permission_profile_denies_write_in_read_only():
    guardrails = AgentGuardrails(tool_permission_profile="read_only")

    profile = build_permission_profile(guardrails)

    assert profile.is_read_only is True
    assert profile.can_use_network is False
    assert profile.decide(ResourceType.TOOL, PermissionAction.WRITE) == PermissionDecision.DENY


def test_guardrails_human_approval_uses_agentscope_default_permission_mode():
    from agentscope.permission import PermissionMode

    guardrails = AgentGuardrails(
        human_approval=True,
        tool_permission_profile="trusted_tools",
    )

    assert _permission_mode_for_guardrails(guardrails) == PermissionMode.DEFAULT


def test_permission_decision_payload_denies_write_for_read_only_profile():
    guardrails = AgentGuardrails(tool_permission_profile="read_only")

    payload = _permission_decision_payload(
        {"tool_call_id": "call-write", "tool_call_name": "Write"},
        guardrails,
    )

    assert payload["resourceType"] == "tool"
    assert payload["resourceId"] == "Write"
    assert payload["action"] == "write"
    assert payload["decision"] == "deny"


def test_permission_decision_payload_maps_unknown_tool_to_mcp_execute():
    guardrails = AgentGuardrails(tool_permission_profile="trusted_tools")

    payload = _permission_decision_payload(
        {"tool_call_id": "call-mcp", "tool_call_name": "SearchDocs"},
        guardrails,
    )

    assert payload["resourceType"] == "mcp"
    assert payload["action"] == "execute"
    assert payload["decision"] == "allow"


def test_pending_hitl_tool_calls_seed_chat_history_tool_runs():
    runs = _pending_tool_calls_to_runs([
        {
            "id": "call-1",
            "name": "AskUser",
            "input": {
                "title": "补充信息",
                "questions": [{"id": "target", "type": "text", "label": "目标"}],
            },
        },
    ])

    assert runs == [
        ChatToolRun(
            id="call-1",
            name="AskUser",
            args='{"title": "补充信息", "questions": [{"id": "target", "type": "text", "label": "目标"}]}',
            status="running",
        ),
    ]


def test_trace_status_completed_after_waiting_is_resolved():
    status = _event_trace_status([
        {"eventType": "SESSION", "status": "running", "sortOrder": 0},
        {"eventType": "HITL_REQUIRED", "status": "waiting", "sortOrder": 1},
        {"eventType": "HITL_RESOLVED", "status": "completed", "sortOrder": 2},
        {"eventType": "ASSISTANT_MESSAGE", "status": "completed", "sortOrder": 3},
    ])

    assert status == "completed"


def test_trace_status_waiting_when_hitl_is_unresolved():
    status = _event_trace_status([
        {"eventType": "SESSION", "status": "running", "sortOrder": 0},
        {"eventType": "HITL_REQUIRED", "status": "waiting", "sortOrder": 1},
    ])

    assert status == "waiting"


def test_trace_status_does_not_complete_for_user_only_messages():
    status = _trace_status([
        SimpleNamespace(role="user", error=None, team_activities=[]),
    ])

    assert status == "empty"


def test_trace_status_completes_when_assistant_message_exists():
    status = _trace_status([
        SimpleNamespace(role="user", error=None, team_activities=[]),
        SimpleNamespace(role="assistant", error=None, team_activities=[]),
    ])

    assert status == "completed"


def test_pending_hitl_tool_calls_preserve_existing_tool_history():
    runs = _pending_tool_calls_to_runs(
        [
            {
                "id": "call-ask",
                "name": "AskUser",
                "input": {"title": "补充信息", "questions": []},
            },
        ],
        [
            ChatToolRun(id="call-read", name="Read", args='{"file_path": "a.md"}', status="completed"),
            ChatToolRun(id="call-grep", name="Grep", args='{"pattern": "todo"}', status="completed"),
        ],
    )

    assert [item.name for item in runs] == ["Read", "Grep", "AskUser"]
    assert runs[0].args == '{"file_path": "a.md"}'
    assert runs[2].args == '{"title": "补充信息", "questions": []}'


def test_background_run_event_buffer_is_bounded(monkeypatch):
    import src.server.routes.chat.background_runs as background_runs

    monkeypatch.setattr(background_runs, "MAX_EVENTS_PER_RUN", 3)
    run = ChatBackgroundRun(id="run-test", session_id="session-test")

    async def emit_events():
        for index in range(5):
            await _broadcast(run, f"event-{index}")

    asyncio.run(emit_events())

    assert run.events == ["event-2", "event-3", "event-4"]


def test_background_run_keeps_waiting_status_after_hitl_required():
    async def run_waiting_case():
        async def factory():
            yield _sse_event("HITL_REQUIRED", {"id": "hitl-1"})

        run = await start_chat_background_run(
            session_id="session-waiting",
            factory=factory,
            task="需要人工确认的任务",
            persist=False,
        )
        assert run.async_task is not None
        await run.async_task
        return run

    run = asyncio.run(run_waiting_case())

    assert run.status == "waiting"
    assert run.completed_at is None


def test_background_run_resume_relay_marks_original_run_completed():
    async def run_resume_case():
        async def initial_factory():
            yield _sse_event("HITL_REQUIRED", {"id": "hitl-1"})

        async def resume_factory():
            yield _sse_event("TEXT_BLOCK_DELTA", {"block_id": "block-1", "delta": "done"})
            yield _sse_event("REPLY_END", {"reply_id": "reply-1"})

        run = await start_chat_background_run(
            session_id="session-resume",
            factory=initial_factory,
            task="需要人工确认的任务",
            persist=False,
        )
        assert run.async_task is not None
        await run.async_task
        assert run.status == "waiting"

        replayed = [
            event
            async for event in relay_chat_background_run_events(run.id, resume_factory())
        ]
        return run, replayed

    run, replayed = asyncio.run(run_resume_case())

    assert run.status == "completed"
    assert run.completed_at is not None
    assert len(replayed) == 2
    assert any("TEXT_BLOCK_DELTA" in event for event in run.events)
