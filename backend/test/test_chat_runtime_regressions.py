import asyncio
from pathlib import Path
from types import SimpleNamespace

from src.server.routes.chat.background_runs import (
    ChatBackgroundRun,
    _broadcast,
    relay_chat_background_run_events,
    start_chat_background_run,
)
from src.server.routes.chat.helpers import _sse_event
from src.server.routes.chat.handlers.resume import (
    _is_final_answer_text_scope,
    _should_emit_raw_resume_event,
    _should_wrap_resume_text_as_final,
)
from src.server.routes.chat.intent_router import (
    IntentRoute,
    route_chat_message,
    route_chat_message_with_model,
    route_trace_payload,
)
from src.server.routes.chat.models import ChatMessage, ChatTeamActivity, ChatTeamMemberPayload, ChatToolRun
from src.server.routes.chat.prompts import build_worker_assignment_text
from src.server.routes.chat.runtime import _permission_mode_for_guardrails
from src.agents.factory import _safe_workspace_dir
from src.agents.runtime_types import WORKSPACE_DIR
from src.storage.database import _normalize_guardrails
from src.server.routes.chat.runtime_builder import (
    RuntimeModelConfig,
    build_main_agent_runtime_spec,
    build_runtime_agent_cache_key,
    build_runtime_model_config,
    create_main_agent_from_spec,
)
from src.server.routes.chat.team_worker_selector import filter_team_members_for_runtime
from src.server.routes.chat.tool_scope_resolver import resolve_runtime_tool_scope
from src.server.routes.chat.team_state import _collect_worker_outputs, _pending_tool_calls_to_runs
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
    assert "summary" in schema["properties"]
    assert "required" not in schema
    assert "under the schema maxLength" in context_config.compression_prompt


def test_resume_text_event_scopes_are_separated():
    assert _is_final_answer_text_scope("single") is True
    assert _is_final_answer_text_scope("leader") is True
    assert _is_final_answer_text_scope("worker") is False
    assert _should_wrap_resume_text_as_final("TEXT_BLOCK_DELTA", "leader") is True
    assert _should_wrap_resume_text_as_final("TEXT_BLOCK_DELTA", "worker") is False


def test_team_leader_collects_formal_worker_report_only():
    """Leader 汇总只消费 Worker report，不读取运行过程 output 碎片。"""

    outputs = _collect_worker_outputs([
        ChatTeamActivity(
            id="activity-worker",
            agentId="worker-a",
            agentName="调研 Worker",
            role="worker",
            status="completed",
            task="调研任务",
            output="过程碎片：正在读取资料...",
            report="正式报告：用户流程已梳理完成。",
            createdAt="2026-06-29T00:00:00+00:00",
        ),
    ])

    assert outputs == [{
        "agentId": "worker-a",
        "name": "调研 Worker",
        "status": "completed",
        "detail": "",
        "report": "正式报告：用户流程已梳理完成。",
        "output": "正式报告：用户流程已梳理完成。",
        "skippedReason": "",
        "reportReviewStatus": "",
        "reportReviewNotes": "",
        "reportReviewGaps": "",
    }]
    assert _should_emit_raw_resume_event("TEXT_BLOCK_DELTA", "leader") is False
    assert _should_emit_raw_resume_event("TEXT_BLOCK_DELTA", "worker") is False
    assert _should_emit_raw_resume_event("THINKING_BLOCK_DELTA", "leader") is False
    assert _should_emit_raw_resume_event("THINKING_BLOCK_DELTA", "single") is True


def test_worker_assignment_uses_member_identity_when_description_is_default():
    research = ChatTeamMemberPayload.model_validate({
        "agentId": "research-agent",
        "name": "调研 Agent",
        "role": "worker",
        "description": "Team worker session",
    })
    developer = ChatTeamMemberPayload.model_validate({
        "agentId": "dev-agent",
        "name": "开发 Agent",
        "role": "worker",
        "description": "Team worker session",
    })

    research_assignment = build_worker_assignment_text(research)
    developer_assignment = build_worker_assignment_text(developer)

    assert research_assignment != developer_assignment
    assert "调研" in research_assignment or "事实" in research_assignment
    assert "实现" in developer_assignment or "代码" in developer_assignment


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


def test_intent_route_only_selects_react_architecture_and_executor():
    route = route_chat_message(team_members=[])

    assert route.mode == "react"
    assert route.runtime.kind == "react"
    assert route.executor.kind == "single_agent"
    assert not hasattr(route, "selected_subagent_ids")
    assert not hasattr(route, "tool_scope")


def test_intent_route_trace_payload_uses_api_aliases():
    route = route_chat_message(team_members=[])

    payload = route_trace_payload(route)

    assert payload["mode"] == "react"
    assert payload["runtime"]["kind"] == "react"
    assert payload["executor"]["kind"] == "single_agent"
    assert "requireClarification" not in payload
    assert "selectedSkillIds" not in payload
    assert "selectedSubagentIds" not in payload


def test_intent_route_team_executor_is_mechanical():
    route = route_chat_message(
        team_members=[
            ChatTeamMemberPayload(
                id="member-worker",
                agentId="worker-agent",
                name="Worker",
                role="worker",
            ),
        ],
    )

    assert route.mode == "react"
    assert route.executor.kind == "team"
    assert route.executor.payload["memberCount"] == 1


def test_filter_team_members_for_runtime_keeps_leader_and_selected_workers():
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

    routed = filter_team_members_for_runtime(
        members=members,
        selected_member_ids=["research-agent"],
        leader_agent_id="leader-agent",
    )

    assert [member.agent_id for member in routed] == ["leader-agent", "research-agent"]


def test_intent_route_tool_scope_stays_minimal_for_conversation():
    decision = resolve_runtime_tool_scope(
        message="你好，解释一下这个概念。",
        agent_config=SimpleNamespace(
            mcp_tools=["mcp-1"],
            knowledge_tools=["kb-1"],
        ),
        has_selected_skills=False,
        has_team=False,
        has_images=False,
    )

    assert decision.scope.allow_mcp is False
    assert decision.scope.allow_knowledge is False
    assert decision.scope.allow_bash is False
    assert decision.scope.allow_http is False


def test_intent_route_tool_scope_enables_execution_and_research_tools():
    decision = resolve_runtime_tool_scope(
        message="请运行 pytest 并搜索知识库资料分析失败原因。",
        agent_config=SimpleNamespace(
            mcp_tools=["mcp-1"],
            knowledge_tools=["kb-1"],
        ),
        has_selected_skills=False,
        has_team=False,
        has_images=False,
    )

    assert decision.scope.allow_bash is True
    assert decision.scope.allow_http is True
    assert decision.scope.allow_knowledge is True


class _FakeTextDeltaEvent:
    def __init__(self, delta: str):
        self.delta = delta

    def model_dump(self, mode: str = "json"):
        return {"type": "TEXT_BLOCK_DELTA", "delta": self.delta}


class _FakeRouterAgent:
    def __init__(self, text: str):
        self.text = text

    async def reply_stream(self, message):
        yield _FakeTextDeltaEvent(self.text)


def test_model_intent_route_ignores_legacy_plan_subagent_and_tool_scope(monkeypatch):
    parent = SimpleNamespace(
        id="parent-agent",
        name="Parent",
        description="",
        callable_agents=["code-agent"],
        plan_config=SimpleNamespace(enabled=True),
        mcp_tools=[],
        knowledge_tools=["kb-1"],
    )
    children = {
        "code-agent": SimpleNamespace(
            id="code-agent",
            name="Code Agent",
            description="负责代码实现、修复和测试。",
            system_prompt="",
        ),
    }
    router_json = """
    {
      "mode": "agent_execution",
      "intent": "code_change",
      "confidence": 0.91,
      "reason": "需要修改代码并运行验证。",
      "shouldCreatePlan": true,
      "requireClarification": false,
      "selectedSubagentIds": ["code-agent"],
      "selectedSkillIds": [],
      "selectedTeamMemberIds": [],
      "toolScope": {
        "allowMcp": true,
        "allowKnowledge": true,
        "allowBash": true,
        "allowHttp": false,
        "reason": "代码修改需要测试，且需要知识库上下文。"
      }
    }
    """

    monkeypatch.setattr(
        "src.server.routes.agents.get_registered_agent",
        lambda agent_id: children.get(agent_id),
    )
    monkeypatch.setattr(
        "src.server.routes.chat.intent_router.get_or_create_agent",
        lambda **kwargs: _FakeRouterAgent(router_json),
    )

    route = asyncio.run(route_chat_message_with_model(
        message="请让代码 Agent 修复项目中的测试问题，并运行 pytest 验证。",
        agent_config=parent,
        team_members=[],
        images=[],
        model_name="test-model",
    ))

    assert route.mode == "react"
    assert route.runtime.kind == "react"
    assert route.executor.kind == "single_agent"
    assert not hasattr(route, "should_create_plan")
    assert not hasattr(route, "selected_subagent_ids")
    assert not hasattr(route, "tool_scope")
    model_decision = next(decision for decision in route.decisions if decision.name == "architecture_route")
    assert model_decision.payload["intent"] == "code_change"


def test_model_intent_route_falls_back_to_rules_on_invalid_json(monkeypatch):
    monkeypatch.setattr(
        "src.server.routes.chat.intent_router.get_or_create_agent",
        lambda **kwargs: _FakeRouterAgent("not json"),
    )

    route = asyncio.run(route_chat_message_with_model(
        message="请修复一下。",
        agent_config=SimpleNamespace(
            id="agent",
            callable_agents=[],
            plan_config=SimpleNamespace(enabled=True),
            mcp_tools=[],
            knowledge_tools=[],
        ),
        team_members=[],
        images=[],
        model_name="test-model",
    ))

    assert route.mode == "react"
    assert route.runtime.kind == "react"
    assert route.executor.kind == "single_agent"


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


def test_runtime_builder_cache_key_includes_tool_scope():
    key = build_runtime_agent_cache_key(
        agent_id="agent",
        session_id="session",
        model_name="model",
        deep_thinking=False,
        fallback_names=["fallback"],
        used_skills=[],
        mcp_servers=[],
        knowledge_tools=[],
        runtime_tool_scope=RuntimeToolScope(allow_mcp=False, allow_knowledge=False, allow_bash=True, allow_http=False),
        guardrail_cache_key="guardrails",
    )

    assert "tools-mcp-0:knowledge-0:bash-1:http-0" in key
    assert "plan-" not in key


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
        runtime_tool_scope=tool_scope,
        guardrail_cache_key="guardrails",
    )

    assert spec.name == "Agent"
    assert spec.parent_agent_id == "agent"
    assert spec.tool_scope is tool_scope
    assert "thinking-True" in spec.runtime_agent_id


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
        runtime_tool_scope=tool_scope,
        guardrail_cache_key="guardrails",
    )

    result = create_main_agent_from_spec(
        spec,
        on_fallback_event=lambda event: None,
    )

    assert result == "agent-instance"
    assert calls["agent_id"] == spec.runtime_agent_id
    assert calls["name"] == "Agent"
    assert calls["model_name"] == "model"
    assert calls["tool_scope"] is tool_scope
    assert callable(calls["on_fallback_event"])


def test_unified_permission_profile_maps_legacy_bypass_to_trusted_tools():
    guardrails = AgentGuardrails(
        tool_permission_mode="bypass",
        tool_permission_profile="",
    )

    profile = build_permission_profile(guardrails)

    assert profile.tool_policy == "trusted_tools"
    assert profile.decide(ResourceType.TOOL, PermissionAction.READ) == PermissionDecision.ALLOW


def test_default_workspace_dir_is_project_root_not_user_home():
    """默认 LocalWorkspace 应收窄到项目根，避免 Glob/Grep 扫用户主目录。"""

    assert WORKSPACE_DIR.endswith("agent-workstation")
    assert WORKSPACE_DIR != str(Path.home())


def test_legacy_root_filesystem_scope_maps_to_project_workspace():
    """历史默认 filesystem_scope='/' 不应被映射到用户主目录。"""

    guardrails = AgentGuardrails(filesystem_scope="/")

    assert _safe_workspace_dir(guardrails) == WORKSPACE_DIR


def test_storage_normalizes_legacy_root_filesystem_scope():
    """历史库中保存的 '/' 读出时也展示为项目根目录。"""

    guardrails = _normalize_guardrails({"filesystem_scope": "/"})

    assert guardrails["filesystem_scope"] == WORKSPACE_DIR


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
