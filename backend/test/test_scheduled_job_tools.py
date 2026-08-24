"""单 Agent ScheduleJob 工具组：立即写库、Team 不注入、flag 关闭明确失败。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from agentscope.message import ToolResultState

from src.scheduler.models import DEFAULT_JOB_TOOL_SCOPE
from src.scheduler.service import ScheduledJobService
from src.scheduler.settings import is_scheduled_jobs_enabled, set_scheduled_jobs_enabled
from src.server.routes.chat.models import ChatMessage
from src.server.routes.chat.react_runtime import _build_runtime_feature_parts
from src.storage import database
from src.storage.database import DEFAULT_PROJECT_ID, DEFAULT_WORKSPACE_ID, ensure_db
from src.tools.scheduled_job_tools import (
    ASKUSER_DELIVERY_DEFAULT,
    ASKUSER_DELIVERY_OPTIONS,
    SCHEDULED_JOBS_DISABLED_MESSAGE,
    ConfirmJob,
    ListJobs,
    PauseJob,
    ScheduleJob,
    build_scheduled_job_tools,
    scheduled_job_prompt_addendum,
    scheduled_job_tools_cache_key,
)


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    """每个用例使用独立 SQLite。"""

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "agent_workstation.db")
    monkeypatch.setattr(database, "_initialized", False)
    yield
    database._initialized = False


def _tools(**overrides):
    ensure_db()
    payload = {
        "workspace_id": DEFAULT_WORKSPACE_ID,
        "project_id": DEFAULT_PROJECT_ID,
        "agent_id": "default",
        "session_id": "session-1",
    }
    payload.update(overrides)
    built = build_scheduled_job_tools(**payload)
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


def test_delivery_askuser_options_lock_notify_first():
    """AskUser 投递选项第一项且默认是「生成后通知我」。"""

    assert ASKUSER_DELIVERY_OPTIONS == ("生成后通知我", "不投递")
    assert ASKUSER_DELIVERY_DEFAULT == "生成后通知我"
    assert "直接发给客户" not in ASKUSER_DELIVERY_OPTIONS
    description = ScheduleJob.description
    assert description.index("生成后通知我") < description.index("不投递")
    assert "不要提供「直接发给客户」" in description
    addendum = scheduled_job_prompt_addendum()
    assert addendum.index("生成后通知我") < addendum.index("不投递")
    assert "Do not offer 直接发给客户" in addendum


def test_tools_are_ordinary_not_external():
    tools = build_scheduled_job_tools(
        workspace_id="ws",
        project_id="p",
        agent_id="a",
        session_id="s",
        service=ScheduledJobService(),
    )
    assert [tool.name for tool in tools] == ["ScheduleJob", "ListJobs", "PauseJob", "ConfirmJob"]
    assert all(tool.is_external_tool is False for tool in tools)
    assert PauseJob.input_schema["required"] == ["job_id"]
    assert "name" not in PauseJob.input_schema["properties"]


def test_schedule_job_writes_pending_confirmation(isolated_db):
    set_scheduled_jobs_enabled(True)
    tools = _tools()
    chunk = _run(tools["ScheduleJob"](**_schedule_kwargs()))
    body = _payload(chunk)

    assert chunk.state == ToolResultState.SUCCESS
    assert body["status"] == "pending_confirmation"
    assert body["toolScope"] == DEFAULT_JOB_TOOL_SCOPE
    stored = ScheduledJobService().get_job(body["jobId"])
    assert stored.status == "pending_confirmation"
    assert stored.created_from_session_id == "session-1"
    assert stored.delivery_policy == "notify"
    assert stored.tool_scope.allow_bash is False


def test_pause_job_updates_same_job(isolated_db):
    set_scheduled_jobs_enabled(True)
    tools = _tools()
    created = _payload(_run(tools["ScheduleJob"](**_schedule_kwargs())))
    job_id = created["jobId"]
    _run(tools["ConfirmJob"](job_id=job_id))

    paused = _run(tools["PauseJob"](job_id=job_id))
    body = _payload(paused)

    assert paused.state == ToolResultState.SUCCESS
    assert body["jobId"] == job_id
    assert body["status"] == "paused"
    stored = ScheduledJobService().get_job(job_id)
    assert stored.status == "paused"
    assert stored.pause_reason == "user"
    listed = _payload(_run(tools["ListJobs"]()))
    assert listed["jobs"][0]["jobId"] == job_id
    assert listed["jobs"][0]["status"] == "paused"


def test_pause_job_requires_job_id_not_name(isolated_db):
    set_scheduled_jobs_enabled(True)
    tools = _tools()
    chunk = _run(tools["PauseJob"](job_id="", name="周五周报"))
    assert chunk.state == ToolResultState.ERROR
    assert "job_id" in _payload(chunk)["error"]


def test_confirm_job_is_idempotent(isolated_db):
    set_scheduled_jobs_enabled(True)
    tools = _tools()
    created = _payload(_run(tools["ScheduleJob"](**_schedule_kwargs())))
    job_id = created["jobId"]

    first = _payload(_run(tools["ConfirmJob"](job_id=job_id)))
    second = _payload(_run(tools["ConfirmJob"](job_id=job_id)))

    assert first["status"] == "active"
    assert second["status"] == "active"
    assert first["nextRunAt"] == second["nextRunAt"]
    assert ScheduledJobService().get_job(job_id).status == "active"


def test_flag_off_write_tools_fail_clearly(isolated_db):
    ensure_db()
    assert is_scheduled_jobs_enabled() is False
    set_scheduled_jobs_enabled(True)
    tools = _tools()
    created = _payload(_run(tools["ScheduleJob"](**_schedule_kwargs(name="先建一条"))))
    set_scheduled_jobs_enabled(False)

    schedule = _run(tools["ScheduleJob"](**_schedule_kwargs()))
    pause = _run(tools["PauseJob"](job_id=created["jobId"]))
    confirm = _run(tools["ConfirmJob"](job_id=created["jobId"]))
    listed = _run(tools["ListJobs"]())

    for chunk in (schedule, pause, confirm):
        assert chunk.state == ToolResultState.ERROR
        assert _payload(chunk)["error"] == SCHEDULED_JOBS_DISABLED_MESSAGE
    assert listed.state == ToolResultState.SUCCESS
    assert _payload(listed)["jobs"][0]["jobId"] == created["jobId"]


def test_team_executor_does_not_inject_scheduled_job_tools(monkeypatch):
    monkeypatch.setattr(
        "src.server.routes.chat.react_runtime.load_subagent_catalog",
        lambda workspace_dir: SimpleNamespace(definitions={}, fingerprint="none"),
    )
    runtime_context = SimpleNamespace(
        project_root=Path("/tmp/project"),
        workspace_id=DEFAULT_WORKSPACE_ID,
        project_id=DEFAULT_PROJECT_ID,
    )
    team_ctx = SimpleNamespace(
        session_id="session-1",
        agent_id="agent-1",
        agent_name="Agent",
        persist_run=True,
        intent_route=SimpleNamespace(executor=SimpleNamespace(kind="team")),
        runtime_context=runtime_context,
    )
    single_ctx = SimpleNamespace(
        **{**team_ctx.__dict__, "intent_route": SimpleNamespace(executor=SimpleNamespace(kind="single_agent"))},
    )

    team_parts = _build_runtime_feature_parts(team_ctx, ChatMessage(message="组队做周报"))
    single_parts = _build_runtime_feature_parts(single_ctx, ChatMessage(message="登记周报"))

    assert [tool.name for tool in team_parts[2]] == []
    assert "scheduled-job-tools" not in team_parts[1]
    assert "ScheduleJob" not in team_parts[5]
    assert [tool.name for tool in single_parts[2]] == [
        "ScheduleJob",
        "ListJobs",
        "PauseJob",
        "ConfirmJob",
    ]
    assert single_parts[1] == scheduled_job_tools_cache_key(single_parts[2])
    assert ASKUSER_DELIVERY_DEFAULT in single_parts[5]
