"""Chat Trace Observation：Runtime 开闭与按 Run 的 API。"""
from __future__ import annotations


import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.server.routes.chat.observation_runtime import (
    close_run_root,
    observe_source_event,
    open_run_root,
)
from src.server.routes.traces import router as traces_router
from src.storage.engine import database
from src.storage.records.chat_run_records import upsert_chat_run
from src.storage.records.observation_records import list_trace_observations


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    yield


@pytest.fixture
def client(isolated_db):
    database.ensure_db()
    app = FastAPI()
    app.include_router(traces_router, prefix="/api/traces")
    with TestClient(app) as test_client:
        yield test_client


def _now_offset(seconds: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def _seed_run(
    *,
    run_id: str,
    session_id: str = "sess-1",
    project_id: str = "default",
    status: str = "completed",
    task: str = "写周报",
    created_at: str | None = None,
    completed_at: str | None = None,
) -> None:
    started = created_at or _now_offset(-10)
    upsert_chat_run({
        "id": run_id,
        "sessionId": session_id,
        "projectSlug": project_id,
        "agentId": "default",
        "agentName": "助手",
        "task": task,
        "mode": "agent",
        "status": status,
        "createdAt": started,
        "completedAt": completed_at if status in {"completed", "failed", "cancelled"} else None,
        "updatedAt": completed_at or started,
    })


def test_single_agent_steps_are_closed_observations(isolated_db) -> None:
    open_run_root(
        persist=True,
        run_id="run-a",
        session_id="sess-a",
        task="查文件",
        agent_name="助手",
    )
    observe_source_event(
        persist=True,
        run_id="run-a",
        session_id="sess-a",
        event_type="MODEL_CALL_START",
        data={"model_name": "deepseek"},
    )
    observe_source_event(
        persist=True,
        run_id="run-a",
        session_id="sess-a",
        event_type="MODEL_CALL_END",
        data={"model_name": "deepseek", "observationText": "先读 README", "finished_reason": "completed"},
    )
    observe_source_event(
        persist=True,
        run_id="run-a",
        session_id="sess-a",
        event_type="TOOL_CALL_START",
        data={"tool_call_id": "c1", "tool_call_name": "Read"},
    )
    observe_source_event(
        persist=True,
        run_id="run-a",
        session_id="sess-a",
        event_type="TOOL_CALL_END",
        data={"tool_call_id": "c1", "tool_call_name": "Read", "input": {"path": "README.md"}},
    )
    observe_source_event(
        persist=True,
        run_id="run-a",
        session_id="sess-a",
        event_type="TOOL_RESULT_END",
        data={"tool_call_id": "c1", "output": "# 文档", "state": "completed"},
    )
    close_run_root(
        persist=True,
        run_id="run-a",
        outcome="completed",
        output={"answer": "已读完"},
    )

    rows = list_trace_observations("run-a")
    kinds = [item["kind"] for item in rows]
    names = [item["name"] for item in rows]
    assert kinds.count("run") == 1
    assert kinds.count("generation") == 1
    assert kinds.count("tool") == 1
    assert "TOOL_CALL_START" not in names
    assert "TOOL_CALL_END" not in names
    tool = next(item for item in rows if item["kind"] == "tool")
    assert tool["name"] == "Read"
    assert tool["input"]["path"] == "README.md"
    assert tool["output"]["result"] == "# 文档"
    assert tool["endedAt"]
    generation = next(item for item in rows if item["kind"] == "generation")
    assert generation["output"]["text"] == "先读 README"
    assert all(item["endedAt"] for item in rows)


def test_tool_result_keeps_streamed_stdout(isolated_db) -> None:
    open_run_root(persist=True, run_id="run-bash", session_id="sess-b", task="天气")
    observe_source_event(
        persist=True, run_id="run-bash", session_id="sess-b",
        event_type="TOOL_CALL_START", data={"tool_call_id": "b1", "tool_call_name": "Bash"},
    )
    observe_source_event(
        persist=True, run_id="run-bash", session_id="sess-b",
        event_type="TOOL_RESULT_TEXT_DELTA", data={"tool_call_id": "b1", "delta": "Wuhan: 24C"},
    )
    observe_source_event(
        persist=True, run_id="run-bash", session_id="sess-b",
        event_type="TOOL_RESULT_TEXT_DELTA", data={"tool_call_id": "b1", "delta": ", cloudy"},
    )
    observe_source_event(
        persist=True, run_id="run-bash", session_id="sess-b",
        event_type="TOOL_RESULT_END", data={"tool_call_id": "b1", "state": "success"},
    )
    tool = next(item for item in list_trace_observations("run-bash") if item["kind"] == "tool")
    assert tool["output"]["result"] == "Wuhan: 24C, cloudy"
    assert tool["output"]["state"] == "success"
    assert tool["endedAt"]


def test_tool_result_accepts_text_blocks(isolated_db) -> None:
    open_run_root(persist=True, run_id="run-block", session_id="sess-b", task="读")
    observe_source_event(
        persist=True, run_id="run-block", session_id="sess-b",
        event_type="TOOL_CALL_START", data={"tool_call_id": "r1", "tool_call_name": "Read"},
    )
    observe_source_event(
        persist=True, run_id="run-block", session_id="sess-b",
        event_type="TOOL_RESULT_END",
        data={"tool_call_id": "r1", "output": [{"type": "text", "text": "# 文档"}], "state": "done"},
    )
    tool = next(item for item in list_trace_observations("run-block") if item["kind"] == "tool")
    assert tool["output"]["result"] == "# 文档"


def test_final_answer_is_recorded_on_generation(isolated_db) -> None:
    open_run_root(persist=True, run_id="run-fa", session_id="sess-f", task="天气")
    observe_source_event(
        persist=True, run_id="run-fa", session_id="sess-f",
        event_type="MODEL_CALL_START",
        data={"model_name": "deepseek-v4-flash", "observationInput": {"model": "deepseek-v4-flash", "messages": []}},
    )
    observe_source_event(
        persist=True, run_id="run-fa", session_id="sess-f",
        event_type="AGENT_FINAL_ANSWER_STARTED",
        data={"payload": {"generation": 1, "direct": True}},
    )
    observe_source_event(
        persist=True, run_id="run-fa", session_id="sess-f",
        event_type="FINAL_ANSWER_DELTA", data={"delta": "武汉今天多云。", "generation": 1},
    )
    observe_source_event(
        persist=True, run_id="run-fa", session_id="sess-f",
        event_type="REPLY_END", data={"finished_reason": "completed"},
    )
    generation = next(item for item in list_trace_observations("run-fa") if item["kind"] == "generation")
    assert not generation["output"].get("toolCalls")
    assert generation["output"]["text"] == "武汉今天多云。"


def test_legacy_named_final_answer_event_still_records_that_tool(isolated_db) -> None:
    open_run_root(persist=True, run_id="run-fa-legacy", session_id="sess-f", task="天气")
    observe_source_event(
        persist=True, run_id="run-fa-legacy", session_id="sess-f",
        event_type="MODEL_CALL_START",
        data={"model_name": "deepseek-v4-flash", "observationInput": {"model": "deepseek-v4-flash", "messages": []}},
    )
    observe_source_event(
        persist=True, run_id="run-fa-legacy", session_id="sess-f",
        event_type="AGENT_FINAL_ANSWER_STARTED",
        data={"payload": {"tool_call_id": "fa1", "tool_call_name": "Read"}},
    )
    generation = next(item for item in list_trace_observations("run-fa-legacy") if item["kind"] == "generation")
    assert generation["output"]["toolCalls"][0]["name"] == "Read"


def test_waiting_does_not_close_root(isolated_db) -> None:
    open_run_root(persist=True, run_id="run-wait", session_id="sess-w", task="问用户")
    observe_source_event(
        persist=True,
        run_id="run-wait",
        session_id="sess-w",
        event_type="HITL_REQUIRED",
        data={"payload": {"interactionId": "ask-1", "toolCalls": [{"name": "AskUser"}]}},
    )
    close_run_root(persist=True, run_id="run-wait", outcome="waiting_user")

    rows = list_trace_observations("run-wait")
    root = next(item for item in rows if item["kind"] == "run")
    approval = next(item for item in rows if item["kind"] == "approval")
    assert root["endedAt"] is None
    assert approval["status"] == "waiting"
    assert approval["endedAt"] is None


def test_resume_appends_to_same_run(isolated_db) -> None:
    open_run_root(persist=True, run_id="run-resume", session_id="sess-r", task="继续")
    observe_source_event(
        persist=True,
        run_id="run-resume",
        session_id="sess-r",
        event_type="HITL_REQUIRED",
        data={"payload": {"interactionId": "ask-2", "toolCalls": [{"name": "AskUser"}]}},
    )
    observe_source_event(
        persist=True,
        run_id="run-resume",
        session_id="sess-r",
        event_type="HITL_RESOLVED",
        data={"payload": {"interactionId": "ask-2", "action": "approve"}},
    )
    observe_source_event(
        persist=True,
        run_id="run-resume",
        session_id="sess-r",
        event_type="MODEL_CALL_START",
        data={"model_name": "m"},
    )
    observe_source_event(
        persist=True,
        run_id="run-resume",
        session_id="sess-r",
        event_type="MODEL_CALL_END",
        data={"model_name": "m", "observationText": "接着写"},
    )
    observe_source_event(
        persist=True,
        run_id="run-resume",
        session_id="sess-r",
        event_type="TOOL_CALL_START",
        data={"tool_call_id": "t2", "tool_call_name": "Write"},
    )
    observe_source_event(
        persist=True,
        run_id="run-resume",
        session_id="sess-r",
        event_type="TOOL_RESULT_END",
        data={"tool_call_id": "t2", "output": "ok", "state": "completed"},
    )
    close_run_root(persist=True, run_id="run-resume", outcome="completed", output={"answer": "好"})

    rows = list_trace_observations("run-resume")
    kinds = [item["kind"] for item in rows]
    assert kinds == ["run", "approval", "generation", "tool"]
    assert all(item["runId"] == "run-resume" for item in rows)
    approval = next(item for item in rows if item["kind"] == "approval")
    assert approval["endedAt"]
    assert approval["output"]["action"] == "approve"


def test_skill_subagent_and_team_task_map(isolated_db) -> None:
    open_run_root(persist=True, run_id="run-x", session_id="sess-x", task="协作")
    observe_source_event(
        persist=True,
        run_id="run-x",
        session_id="sess-x",
        event_type="SKILL_CONTEXT",
        data={"skills": [{"id": "demo", "name": "DemoSkill"}]},
    )
    observe_source_event(
        persist=True,
        run_id="run-x",
        session_id="sess-x",
        event_type="SKILL_END",
        data={"skills": [{"id": "demo", "name": "DemoSkill"}], "status": "completed"},
    )
    observe_source_event(
        persist=True,
        run_id="run-x",
        session_id="sess-x",
        event_type="SUBAGENT_STARTED",
        data={"payload": {"invocationId": "inv-1", "subagentName": "researcher", "taskPreview": "调研"}},
    )
    observe_source_event(
        persist=True,
        run_id="run-x",
        session_id="sess-x",
        event_type="SUBAGENT_COMPLETED",
        data={"payload": {"invocationId": "inv-1", "resultPreview": "完成"}},
    )
    observe_source_event(
        persist=True,
        run_id="run-x",
        session_id="sess-x",
        event_type="TEAM_DOMAIN_EVENT",
        data={"type": "task.created", "taskId": "task-1", "payload": {"task": {"id": "task-1", "title": "写稿", "goal": "大纲"}}},
    )
    observe_source_event(
        persist=True,
        run_id="run-x",
        session_id="sess-x",
        event_type="TEAM_DOMAIN_EVENT",
        data={"type": "task.completed", "taskId": "task-1", "payload": {"task": {"id": "task-1", "result": "已交"}}},
    )
    observe_source_event(
        persist=True,
        run_id="run-x",
        session_id="sess-x",
        event_type="GROUP_MESSAGE",
        data={"text": "群聊不应写入"},
    )
    close_run_root(persist=True, run_id="run-x", outcome="completed")

    rows = list_trace_observations("run-x")
    kinds = {item["kind"] for item in rows}
    assert kinds == {"run", "skill", "subagent", "team-task"}
    assert all(item["kind"] != "GROUP_MESSAGE" for item in rows)
    assert next(item for item in rows if item["kind"] == "skill")["name"] == "DemoSkill"
    assert next(item for item in rows if item["kind"] == "team-task")["name"] == "写稿"


def test_parallel_tools_can_overlap(isolated_db) -> None:
    open_run_root(persist=True, run_id="run-p", session_id="sess-p", task="并行")
    observe_source_event(
        persist=True, run_id="run-p", session_id="sess-p",
        event_type="TOOL_CALL_START", data={"tool_call_id": "a", "tool_call_name": "Read"},
    )
    observe_source_event(
        persist=True, run_id="run-p", session_id="sess-p",
        event_type="TOOL_CALL_START", data={"tool_call_id": "b", "tool_call_name": "Grep"},
    )
    observe_source_event(
        persist=True, run_id="run-p", session_id="sess-p",
        event_type="TOOL_RESULT_END", data={"tool_call_id": "b", "output": "g", "state": "completed"},
    )
    observe_source_event(
        persist=True, run_id="run-p", session_id="sess-p",
        event_type="TOOL_RESULT_END", data={"tool_call_id": "a", "output": "r", "state": "completed"},
    )
    tools = [item for item in list_trace_observations("run-p") if item["kind"] == "tool"]
    assert {item["name"] for item in tools} == {"Read", "Grep"}
    assert all(item["endedAt"] for item in tools)


def test_trace_api_lists_runs_and_rejects_session_id(client) -> None:
    _seed_run(run_id="run-one", session_id="sess-shared", task="第一轮", status="completed")
    _seed_run(run_id="run-two", session_id="sess-shared", task="第二轮", status="cancelled")
    _seed_run(run_id="run-other", project_id="other", task="别的项目", status="completed")
    open_run_root(persist=True, run_id="run-one", session_id="sess-shared", task="第一轮")
    close_run_root(persist=True, run_id="run-one", outcome="completed", output={"answer": "好"})

    listed = client.get("/api/traces?projectSlug=default").json()
    ids = [item["id"] for item in listed]
    assert "run-one" in ids
    assert "run-two" in ids
    assert "run-other" not in ids
    assert all(item["id"] != "sess-shared" for item in listed)
    cancelled = next(item for item in listed if item["id"] == "run-two")
    assert cancelled["status"] == "cancelled"
    assert cancelled["durationMs"] is not None

    missing = client.get("/api/traces/sess-shared?projectSlug=default")
    assert missing.status_code == 404

    detail = client.get("/api/traces/run-one?projectSlug=default").json()
    assert detail["id"] == "run-one"
    assert detail["status"] == "completed"
    assert [item["kind"] for item in detail["observations"]] == ["run"]
    assert "events" not in detail
    assert "messages" not in detail

    empty = client.get("/api/traces/run-two?projectSlug=default").json()
    assert empty["status"] == "cancelled"
    assert empty["observations"][0]["kind"] == "run"
    assert empty["observations"][0]["input"]["message"] == "第二轮"
    assert empty["observations"][0]["metadata"]["synthetic"] is True


def test_background_run_persist_writes_root_and_leaves_waiting_open(isolated_db) -> None:
    from src.server.routes.chat.background_runs import start_chat_background_run
    from src.server.routes.chat.helpers import source_event

    async def run_case():
        async def factory():
            yield source_event("HITL_REQUIRED", {
                "id": "hitl-1",
                "payload": {"interactionId": "hitl-1", "toolCalls": [{"name": "AskUser"}]},
            })

        run = await start_chat_background_run(
            session_id="sess-bg",
            factory=factory,
            task="等一下",
            persist=True,
            agent_name="助手",
        )
        assert run.async_task is not None
        await run.async_task
        return run

    run = asyncio.run(run_case())
    rows = list_trace_observations(run.id)
    root = next(item for item in rows if item["kind"] == "run")
    assert run.status == "waiting_user"
    assert root["endedAt"] is None
    assert root["input"]["message"] == "等一下"
    assert any(item["kind"] == "approval" for item in rows)


def test_background_run_persist_closes_root_on_complete(isolated_db) -> None:
    from src.server.routes.chat.background_runs import start_chat_background_run
    from src.server.routes.chat.helpers import source_event

    async def run_case():
        async def factory():
            yield source_event("MODEL_CALL_START", {"model_name": "m"})
            yield source_event("MODEL_CALL_END", {"model_name": "m", "observationText": "直接回答"})

        run = await start_chat_background_run(
            session_id="sess-done",
            factory=factory,
            task="打个招呼",
            persist=True,
        )
        assert run.async_task is not None
        await run.async_task
        return run

    run = asyncio.run(run_case())
    rows = list_trace_observations(run.id)
    root = next(item for item in rows if item["kind"] == "run")
    generation = next(item for item in rows if item["kind"] == "generation")
    assert run.status == "completed"
    assert root["endedAt"]
    assert root["output"]["outcome"] == "completed"
    assert generation["output"]["text"] == "直接回答"


def test_generation_keeps_real_input_and_delayed_output(isolated_db) -> None:
    open_run_root(persist=True, run_id="run-gen", session_id="sess-g", task="武汉天气")
    observe_source_event(
        persist=True,
        run_id="run-gen",
        session_id="sess-g",
        event_type="MODEL_CALL_START",
        data={
            "model_name": "deepseek-v4-flash",
            "observationInput": {
                "model": "deepseek-v4-flash",
                "system": "你是办公助手。",
                "summary": "",
                "messages": [{"role": "user", "text": "帮我看看今天武汉天气怎么样"}],
            },
        },
    )
    observe_source_event(
        persist=True,
        run_id="run-gen",
        session_id="sess-g",
        event_type="MODEL_CALL_END",
        data={"model_name": "deepseek-v4-flash", "finished_reason": "completed", "input_tokens": 812, "output_tokens": 46},
    )
    mid = next(item for item in list_trace_observations("run-gen") if item["kind"] == "generation")
    assert mid["endedAt"] is None
    assert mid["input"]["messages"][0]["text"] == "帮我看看今天武汉天气怎么样"
    assert mid["output"]["usage"]["inputTokens"] == 812
    assert mid["output"]["text"] == ""

    observe_source_event(
        persist=True,
        run_id="run-gen",
        session_id="sess-g",
        event_type="THINKING_BLOCK_DELTA",
        data={"delta": "先查天气。"},
    )
    observe_source_event(
        persist=True,
        run_id="run-gen",
        session_id="sess-g",
        event_type="TOOL_CALL_START",
        data={"tool_call_id": "c1", "tool_call_name": "Bash"},
    )
    observe_source_event(
        persist=True,
        run_id="run-gen",
        session_id="sess-g",
        event_type="TOOL_CALL_END",
        data={"tool_call_id": "c1", "tool_call_name": "Bash", "input": '{"command": "curl wttr.in/Wuhan"}'},
    )
    observe_source_event(
        persist=True,
        run_id="run-gen",
        session_id="sess-g",
        event_type="HITL_REQUIRED",
        data={"payload": {"interactionId": "perm-1", "toolCalls": [{"name": "Bash"}]}},
    )

    generation = next(item for item in list_trace_observations("run-gen") if item["kind"] == "generation")
    assert generation["endedAt"]
    assert generation["input"]["system"] == "你是办公助手。"
    assert generation["output"]["thinking"] == "先查天气。"
    assert generation["output"]["toolCalls"][0]["name"] == "Bash"
    assert generation["output"]["toolCalls"][0]["input"]["command"] == "curl wttr.in/Wuhan"
    assert generation["output"]["usage"]["outputTokens"] == 46
    tool = next(item for item in list_trace_observations("run-gen") if item["kind"] == "tool")
    assert tool["input"]["command"] == "curl wttr.in/Wuhan"


def test_generation_closes_on_reply_end_and_next_start(isolated_db) -> None:
    open_run_root(persist=True, run_id="run-loop", session_id="sess-l", task="继续")
    observe_source_event(
        persist=True, run_id="run-loop", session_id="sess-l",
        event_type="MODEL_CALL_START",
        data={"model_name": "m", "observationInput": {"model": "m", "messages": [{"role": "user", "text": "hi"}]}},
    )
    observe_source_event(
        persist=True, run_id="run-loop", session_id="sess-l",
        event_type="MODEL_CALL_END",
        data={"input_tokens": 10, "output_tokens": 4},
    )
    observe_source_event(
        persist=True, run_id="run-loop", session_id="sess-l",
        event_type="TEXT_BLOCK_DELTA",
        data={"delta": "先读文件"},
    )
    observe_source_event(
        persist=True, run_id="run-loop", session_id="sess-l",
        event_type="TOOL_CALL_END",
        data={"tool_call_id": "r1", "tool_call_name": "Read", "input": {"path": "a.md"}},
    )
    observe_source_event(
        persist=True, run_id="run-loop", session_id="sess-l",
        event_type="TOOL_RESULT_START",
        data={"tool_call_id": "r1"},
    )
    first = next(item for item in list_trace_observations("run-loop") if item["kind"] == "generation")
    assert first["endedAt"]
    assert first["output"]["text"] == "先读文件"
    assert first["output"]["toolCalls"][0]["name"] == "Read"

    observe_source_event(
        persist=True, run_id="run-loop", session_id="sess-l",
        event_type="MODEL_CALL_START",
        data={"model_name": "m", "observationInput": {"model": "m", "messages": [{"role": "assistant", "text": "先读文件"}]}},
    )
    observe_source_event(
        persist=True, run_id="run-loop", session_id="sess-l",
        event_type="TEXT_BLOCK_DELTA",
        data={"delta": "答案来了"},
    )
    observe_source_event(
        persist=True, run_id="run-loop", session_id="sess-l",
        event_type="REPLY_END",
        data={"finished_reason": "completed"},
    )
    generations = [item for item in list_trace_observations("run-loop") if item["kind"] == "generation"]
    assert len(generations) == 2
    assert generations[1]["output"]["text"] == "答案来了"
    assert generations[1]["endedAt"]


def test_trace_api_waiting_matches_chat_run(client) -> None:
    _seed_run(run_id="run-w", task="等用户", status="waiting_user", completed_at=None)
    open_run_root(persist=True, run_id="run-w", session_id="sess-1", task="等用户")
    observe_source_event(
        persist=True,
        run_id="run-w",
        session_id="sess-1",
        event_type="HITL_REQUIRED",
        data={"payload": {"interactionId": "i1", "toolCalls": [{"name": "AskUser"}]}},
    )

    listed = client.get("/api/traces?projectSlug=default").json()
    row = next(item for item in listed if item["id"] == "run-w")
    assert row["status"] == "waiting_user"
    detail = client.get("/api/traces/run-w?projectSlug=default").json()
    assert detail["status"] == "waiting_user"
    approval = next(item for item in detail["observations"] if item["kind"] == "approval")
    assert approval["status"] == "waiting"
    assert approval["endedAt"] is None
