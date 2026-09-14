"""ScheduleJob / PauseJob 工具层集成回归：pending→REST confirm、Pause 同 Job、AskUser 不激活。"""

from __future__ import annotations

import asyncio
import json

import pytest
from agentscope.message import ToolResultState
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.scheduler.service import ScheduledJobService
from src.scheduler.settings import set_scheduled_jobs_enabled
from src.server.routes import scheduled_jobs
from src.server.routes.chat.handlers.resume import resume_interaction_events
from src.server.routes.chat.hitl import PENDING_AGENTS, PENDING_INTERACTIONS, _create_pending_interaction
from src.server.routes.chat.models import ChatResumeRequest
from src.storage import database
from src.storage.database import DEFAULT_PROJECT_ID, DEFAULT_WORKSPACE_ID, ensure_db
from src.tools.scheduled_job_tools import ASKUSER_DELIVERY_OPTIONS, build_scheduled_job_tools


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    """每个用例使用独立 SQLite。"""

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "agent_workstation.db")
    monkeypatch.setattr(database, "_initialized", False)
    monkeypatch.setattr("src.config.runtime_paths.USER_DATA_ROOT", tmp_path / "user-data")
    yield
    database._initialized = False


@pytest.fixture()
def client(isolated_db):
    ensure_db()
    set_scheduled_jobs_enabled(True)
    app = FastAPI()
    app.include_router(scheduled_jobs.router, prefix="/api")
    with TestClient(app) as test_client:
        yield test_client


class _FakeAgentScopeEvent:
    def __init__(self, payload: dict):
        self.payload = payload

    def model_dump(self, mode: str = "json") -> dict:
        return dict(self.payload)


class _FakeAskUserResumeAgent:
    """AskUser 恢复后只给文案，不调用 ConfirmJob。"""

    def __init__(self) -> None:
        self.resume_events: list[object] = []

    async def reply_stream(self, resume_event):
        self.resume_events.append(resume_event)
        yield _FakeAgentScopeEvent({
            "type": "TOOL_CALL_START",
            "reply_id": "reply-askuser-resume",
            "tool_call_id": "final-askuser-resume",
            "tool_call_name": "FinalAnswerStart",
        })
        yield _FakeAgentScopeEvent({
            "type": "TEXT_BLOCK_DELTA",
            "reply_id": "reply-askuser-resume",
            "delta": "字段已记下。任务仍待启用，本轮 AskUser 恢复不会自动激活。",
        })
        yield _FakeAgentScopeEvent({
            "type": "REPLY_END",
            "reply_id": "reply-askuser-resume",
            "finished_reason": "completed",
        })


def _tools():
    ensure_db()
    set_scheduled_jobs_enabled(True)
    built = build_scheduled_job_tools(
        workspace_id=DEFAULT_WORKSPACE_ID,
        project_id=DEFAULT_PROJECT_ID,
        agent_id="default",
        session_id="session-register-1",
    )
    return {tool.name: tool for tool in built}


def _payload(chunk) -> dict:
    return json.loads(chunk.content[0].text)


def _run(coro):
    return asyncio.run(coro)


def _schedule_kwargs(**overrides) -> dict:
    payload = {
        "name": "周五周报",
        "schedule_kind": "weekly",
        "schedule_expr": {"weekdays": ["fri"], "at": "18:00"},
        "timezone": "Asia/Shanghai",
        "frozen_prompt": "生成本周周报，写入 outputs/weekly-report.md",
    }
    payload.update(overrides)
    return payload


def test_schedule_job_pending_then_rest_confirm_activates(client):
    """ScheduleJob 只写 pending_confirmation；Jobs 页 REST confirm 仍可作为启用路径。"""

    tools = _tools()
    created = _payload(_run(tools["ScheduleJob"](**_schedule_kwargs())))
    job_id = created["jobId"]

    assert created["status"] == "pending_confirmation"
    listed = client.get("/api/scheduled-jobs", params={"projectId": DEFAULT_PROJECT_ID})
    assert listed.status_code == 200
    rest_job = next(item for item in listed.json()["jobs"] if item["id"] == job_id)
    assert rest_job["status"] == "pending_confirmation"
    assert rest_job["nextRunAt"] in (None, "")

    confirmed = client.post(f"/api/scheduled-jobs/{job_id}/confirm")
    assert confirmed.status_code == 200
    body = confirmed.json()
    assert body["id"] == job_id
    assert body["status"] == "active"
    assert body["nextRunAt"]
    stored = ScheduledJobService().get_job(job_id)
    assert stored.status == "active"
    assert stored.next_run_at == body["nextRunAt"]


def test_confirm_job_tool_activates_same_as_rest(client):
    """对话 ConfirmJob 与 REST confirm 走同一 confirm()，都能把任务变成 active。"""

    tools = _tools()
    created = _payload(_run(tools["ScheduleJob"](**_schedule_kwargs(name="对话启用周报"))))
    job_id = created["jobId"]
    assert created["status"] == "pending_confirmation"
    assert "ConfirmJob(" in created["message"]

    confirmed = _run(tools["ConfirmJob"](job_id=job_id))
    body = _payload(confirmed)
    assert confirmed.state == ToolResultState.SUCCESS
    assert body["status"] == "active"
    assert body["nextRunAt"]
    assert "任务已启用" in body["message"]

    rest = client.get(f"/api/scheduled-jobs/{job_id}")
    assert rest.status_code == 200
    assert rest.json()["status"] == "active"
    assert rest.json()["nextRunAt"] == body["nextRunAt"]


def test_list_jobs_then_pause_job_updates_same_job_id(client):
    """关掉周五那个周报：ListJobs 拿到 job_id 后 PauseJob 更新同一条。"""

    tools = _tools()
    created = _payload(_run(tools["ScheduleJob"](**_schedule_kwargs())))
    job_id = created["jobId"]
    assert client.post(f"/api/scheduled-jobs/{job_id}/confirm").status_code == 200

    listed = _payload(_run(tools["ListJobs"](status="active")))
    assert [item["jobId"] for item in listed["jobs"]] == [job_id]
    assert listed["jobs"][0]["status"] == "active"

    paused = _run(tools["PauseJob"](job_id=job_id))
    body = _payload(paused)
    assert paused.state == ToolResultState.SUCCESS
    assert body["jobId"] == job_id
    assert body["status"] == "paused"

    listed_after = _payload(_run(tools["ListJobs"]()))
    assert listed_after["jobs"][0]["jobId"] == job_id
    assert listed_after["jobs"][0]["status"] == "paused"
    rest = client.get(f"/api/scheduled-jobs/{job_id}")
    assert rest.status_code == 200
    assert rest.json()["status"] == "paused"
    assert rest.json()["pauseReason"] == "user"
    assert ScheduledJobService().get_job(job_id).id == job_id


def test_askuser_resume_does_not_activate_pending_job(client, monkeypatch):
    """AskUser resume 只收集字段；即使用户说确认启用，Job 仍保持 pending_confirmation。"""

    monkeypatch.setattr(
        "src.server.routes.chat.completion._schedule_memory_reflection",
        lambda *args, **kwargs: None,
    )
    confirm_calls: list[str] = []
    original_confirm = ScheduledJobService.confirm

    def _tracking_confirm(self, job_id: str, *, now=None):
        confirm_calls.append(job_id)
        return original_confirm(self, job_id, now=now)

    monkeypatch.setattr(ScheduledJobService, "confirm", _tracking_confirm)

    tools = _tools()
    created = _payload(_run(tools["ScheduleJob"](**_schedule_kwargs(name="待确认周报"))))
    job_id = created["jobId"]
    assert created["status"] == "pending_confirmation"

    agent = _FakeAskUserResumeAgent()
    askuser_input = json.dumps({
        "title": "登记周五周报",
        "questions": [{
            "id": "delivery",
            "type": "choice",
            "label": "生成后如何处理？",
            "options": list(ASKUSER_DELIVERY_OPTIONS),
            "required": True,
        }],
    }, ensure_ascii=False)

    async def run_case():
        PENDING_INTERACTIONS.clear()
        PENDING_AGENTS.clear()
        interaction = _create_pending_interaction(
            interaction_type="REQUIRE_EXTERNAL_EXECUTION",
            agent=agent,
            session_id="session-register-1",
            agent_id="default",
            agent_name="Agent",
            model_name="deepseek-chat",
            reply_id="reply-askuser-resume",
            tool_calls=[{
                "type": "tool_call",
                "id": "call-askuser",
                "name": "AskUser",
                "input": askuser_input,
                "state": "submitted",
            }],
            message="每周五生成周报发给客户",
            assistant_text="",
            skill_runs=[],
            scope="single_agent",
            persist=False,
        )
        stream = resume_interaction_events(
            ChatResumeRequest.model_validate({
                "interactionId": interaction.id,
                "action": "approve",
                "externalResults": {
                    "call-askuser": "确认启用；投递选「生成后通知我」",
                },
            }),
        )
        return [event async for event in stream]

    try:
        events = _run(run_case())
        assert agent.resume_events
        assert confirm_calls == []
        stored = ScheduledJobService().get_job(job_id)
        assert stored.status == "pending_confirmation"
        assert stored.next_run_at in (None, "")
        rest = client.get(f"/api/scheduled-jobs/{job_id}")
        assert rest.status_code == 200
        assert rest.json()["status"] == "pending_confirmation"
        assert any(
            "仍待启用" in json.dumps(event.data, ensure_ascii=False, default=str)
            for event in events
        )
    finally:
        PENDING_INTERACTIONS.clear()
        PENDING_AGENTS.clear()


@pytest.mark.skip(reason="Playwright 不是第一批门禁；管理页 E2E 可选")
def test_scheduled_jobs_page_playwright_optional():
    """Jobs 页确认启用的浏览器回归暂不作为合入门禁。"""

    pytest.importorskip("playwright")
    raise AssertionError("Playwright 未接入，不应成为合入门禁")
