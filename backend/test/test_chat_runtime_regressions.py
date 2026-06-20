import asyncio

from src.server.routes.chat.background_runs import (
    ChatBackgroundRun,
    _broadcast,
    relay_chat_background_run_events,
    start_chat_background_run,
)
from src.server.routes.chat.helpers import _sse_event
from src.server.routes.chat.models import ChatToolRun
from src.server.routes.chat.team_state import _pending_tool_calls_to_runs
from src.server.routes.chat.trace_recorder import _model_cache_from_event
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
