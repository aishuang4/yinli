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
from src.server.routes.chat.models import ChatToolRun
from src.server.routes.chat.runtime import _permission_mode_for_guardrails
from src.server.routes.chat.team_state import _pending_tool_calls_to_runs
from src.server.routes.chat.trace_recorder import _model_cache_from_event, _permission_decision_payload
from src.server.routes.traces import _event_trace_status, _trace_status
from src.agents.factory import AgentGuardrails, _build_context_config
from src.security.permissions import (
    PermissionAction,
    PermissionDecision,
    ResourceType,
    build_permission_profile,
)
from src.tools.ask_user import AskUser
from src.tools.plan_runtime import CompletePlanStep
from src.server.routes.chat.plans import create_runtime_plan
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
