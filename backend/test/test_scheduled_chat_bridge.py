"""start_scheduled_chat_run 领域 API：立即返回、等待/超时、禁止 rerun。"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from src.agents.factory import AgentGuardrails, AgentModelOptions
from src.agents.runtime_types import RuntimeToolScope
from src.project_files import project_job_run_directory
from src.scheduler.chat_bridge import (
    DEFAULT_FIRING_TIMEOUT_SECONDS,
    ScheduledChatLaunchError,
    ScheduledRunRequest,
    await_scheduled_chat_run,
    scan_scheduled_run_outputs,
    scheduled_chat_run_timeout_seconds,
    start_scheduled_chat_run,
)
from src.server.routes.chat import background_runs as background_runs_module
from src.server.routes.chat.background_runs import get_chat_background_run
from src.server.routes.chat.handlers.runs import rerun_run
from src.server.routes.chat.intent_router import IntentRoute
from src.server.routes.chat.models import ChatMessage
from src.server.routes.chat.readiness_gate import ReadinessDecision
from src.server.routes.chat.run_context import ChatRunContext
from src.server.routes.chat.runtime_builder import RuntimeModelConfig
from src.storage import database
from src.storage.database import (
    DEFAULT_PROJECT_ID,
    DEFAULT_WORKSPACE_ID,
    create_project,
    ensure_db,
    get_chat_run,
    load_chat_store,
)


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    """每个用例独立 SQLite，并收口后台 Chat Run。"""

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "agent_workstation.db")
    monkeypatch.setattr(database, "_initialized", False)
    yield
    for run in list(background_runs_module._runs.values()):
        if run.async_task is not None and not run.async_task.done():
            run.async_task.cancel()
    background_runs_module._runs.clear()
    database._initialized = False


def _frozen_scope() -> RuntimeToolScope:
    return RuntimeToolScope(
        allow_mcp=False,
        allow_knowledge=False,
        allow_bash=False,
        allow_http=False,
        allow_web=False,
    )


def _prepare_project(tmp_path) -> dict:
    ensure_db()
    root = tmp_path / "job-project"
    root.mkdir()
    return create_project({
        "id": "proj-sched",
        "workspace_id": DEFAULT_WORKSPACE_ID,
        "name": "定时项目",
        "root_path": str(root),
    })


def _request(**overrides) -> ScheduledRunRequest:
    payload = {
        "job_id": "job-bridge1",
        "firing_id": "firing-bridge1",
        "workspace_id": DEFAULT_WORKSPACE_ID,
        "project_id": DEFAULT_PROJECT_ID,
        "agent_id": "default",
        "frozen_prompt": "生成本周周报，写入 outputs/weekly-report.md",
        "skill_ids": [],
        "tool_scope": _frozen_scope(),
        "scheduled_for": "2026-08-21T10:00:00+00:00",
        "trigger": "schedule",
    }
    payload.update(overrides)
    return ScheduledRunRequest(**payload)


def _fake_context(run_data: ChatMessage, runtime_context) -> ChatRunContext:
    return ChatRunContext(
        session_id=run_data.session_id or "",
        runtime_context=runtime_context,
        agent_id=run_data.agent_id or "default",
        agent_name=run_data.agent_name or "Assistant",
        agent_config=None,
        persist_run=True,
        runtime_model_settings=SimpleNamespace(
            provider="deepseek",
            api_key="test-key",
            default_model="model",
            fallback_models=[],
        ),
        runtime_model_config=RuntimeModelConfig(
            model_name="model",
            fallback_names=[],
            model_options=AgentModelOptions(max_react_iters=40),
        ),
        readiness=ReadinessDecision(ready=True, source="rule"),
        intent_route=IntentRoute(),
        effective_user_message=run_data.message,
        used_skills=[],
        used_skill_details=[],
        skill_runs=[],
        active_skill_run=None,
        model_name="model",
        system_prompt="prompt",
        guardrails=AgentGuardrails(),
        runtime_tool_scope=_frozen_scope(),
        mcp_servers=[],
        knowledge_tools=[],
    )


def _patch_fake_agent(monkeypatch, execute):
    async def fake_build(run_data, runtime_context):
        return _fake_context(run_data, runtime_context)

    monkeypatch.setattr("src.scheduler.chat_bridge.build_chat_run_context", fake_build)
    monkeypatch.setattr("src.scheduler.chat_bridge.execute_chat_runtime", execute)
    monkeypatch.setattr("src.scheduler.chat_bridge.get_startup_error", lambda ctx, data: None)
    monkeypatch.setattr(
        "src.server.routes.chat.handlers.send.start_chat_run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("禁止直接调 start_chat_run")),
    )


async def _empty_execute(*, ctx, run_data):
    _ = ctx, run_data
    if False:
        yield


def test_chat_message_forbids_trigger_source():
    """公开 ChatMessage 不得出现 trigger_source。"""

    with pytest.raises(ValidationError):
        ChatMessage(message="周报", trigger_source="scheduled_job")


def test_project_job_run_directory_layout(tmp_path):
    directory = project_job_run_directory(tmp_path, "job-abc", "firing-xyz")
    assert directory == tmp_path / ".agent-workstation" / "jobs" / "job-abc" / "runs" / "firing-xyz"
    assert directory.is_dir()
    with pytest.raises(ValueError, match="Job ID"):
        project_job_run_directory(tmp_path, "../escape", "firing-xyz")


def test_scan_scheduled_run_outputs_hashes_files(tmp_path):
    workspace = project_job_run_directory(tmp_path, "job-scan", "firing-scan")
    output = workspace / "outputs" / "weekly-report.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    content = "# 周报\n".encode("utf-8")
    output.write_bytes(content)

    manifest = scan_scheduled_run_outputs(workspace)

    assert manifest["files"] == [{
        "path": "outputs/weekly-report.md",
        "sha256": hashlib.sha256(content).hexdigest(),
        "bytes": len(content),
    }]
    written = (workspace / "manifest.json").read_text(encoding="utf-8")
    assert "weekly-report.md" in written


def test_scheduled_timeout_is_capped_at_900(monkeypatch):
    monkeypatch.setattr(
        "src.scheduler.chat_bridge.get_registered_agent",
        lambda agent_id: SimpleNamespace(guardrails=SimpleNamespace(timeout_seconds=3600)),
    )
    assert scheduled_chat_run_timeout_seconds("agent-1") == DEFAULT_FIRING_TIMEOUT_SECONDS


def test_start_scheduled_chat_run_returns_id_and_persists_columns(isolated_db, tmp_path, monkeypatch):
    project = _prepare_project(tmp_path)
    release = asyncio.Event()

    async def hanging_execute(*, ctx, run_data):
        output = Path(ctx.session_workspace_dir) / "outputs" / "weekly-report.md"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("# 周报\n", encoding="utf-8")
        await release.wait()
        if False:
            yield

    _patch_fake_agent(monkeypatch, hanging_execute)

    async def run_case():
        req = _request(project_id=project["id"])
        chat_run_id = await start_scheduled_chat_run(req)
        assert chat_run_id.startswith("run-")
        record = get_chat_run(chat_run_id)
        assert record is not None
        assert record["triggerSource"] == "scheduled_job"
        assert record["scheduledJobId"] == req.job_id
        assert record["scheduledFiringId"] == req.firing_id
        live = await get_chat_background_run(chat_run_id)
        assert live is not None
        assert live.status == "running"
        store = load_chat_store()
        session = next(item for item in store["sessions"] if item["id"] == live.session_id)
        assert session["kind"] == "scheduled_job"
        workspace = project_job_run_directory(
            Path(project["rootPath"]), req.job_id, req.firing_id,
        )
        assert (workspace / "spec.json").is_file()
        assert (workspace / "outputs").is_dir()
        release.set()
        status = await await_scheduled_chat_run(chat_run_id, timeout_seconds=2)
        assert status == "completed"
        persisted = get_chat_run(chat_run_id)
        assert persisted is not None
        assert persisted["status"] == "completed"
        assert persisted["triggerSource"] == "scheduled_job"
        assert persisted["scheduledJobId"] == req.job_id
        assert persisted["scheduledFiringId"] == req.firing_id
        store_after = load_chat_store()
        session_after = next(
            item for item in store_after["sessions"] if item["id"] == live.session_id
        )
        assert session_after["kind"] == "scheduled_job"
        manifest = scan_scheduled_run_outputs(workspace)
        assert [item["path"] for item in manifest["files"]] == ["outputs/weekly-report.md"]
        return live.session_id

    session_id = asyncio.run(run_case())
    assert session_id.startswith("sched-")


def test_each_firing_creates_a_new_scheduled_session(isolated_db, tmp_path, monkeypatch):
    project = _prepare_project(tmp_path)
    _patch_fake_agent(monkeypatch, _empty_execute)

    async def run_case():
        first = await start_scheduled_chat_run(
            _request(project_id=project["id"], firing_id="firing-a"),
        )
        second = await start_scheduled_chat_run(
            _request(project_id=project["id"], firing_id="firing-b"),
        )
        await await_scheduled_chat_run(first, timeout_seconds=2)
        await await_scheduled_chat_run(second, timeout_seconds=2)
        left = await get_chat_background_run(first)
        right = await get_chat_background_run(second)
        assert left is not None and right is not None
        assert left.session_id != right.session_id
        store = load_chat_store()
        kinds = {
            item["id"]: item["kind"]
            for item in store["sessions"]
            if item["id"] in {left.session_id, right.session_id}
        }
        assert kinds[left.session_id] == "scheduled_job"
        assert kinds[right.session_id] == "scheduled_job"

    asyncio.run(run_case())


def test_await_timeout_cancels_chat_background_run(isolated_db, tmp_path, monkeypatch):
    project = _prepare_project(tmp_path)

    async def never_finish(*, ctx, run_data):
        _ = ctx, run_data
        await asyncio.sleep(30)
        if False:
            yield

    _patch_fake_agent(monkeypatch, never_finish)

    async def run_case():
        chat_run_id = await start_scheduled_chat_run(
            _request(project_id=project["id"], firing_id="firing-timeout"),
        )
        status = await await_scheduled_chat_run(chat_run_id, timeout_seconds=0.2)
        assert status == "cancelled"
        run = await get_chat_background_run(chat_run_id)
        assert run is not None
        assert run.status == "cancelled"
        record = get_chat_run(chat_run_id)
        assert record is not None
        assert record["status"] == "cancelled"
        assert record["triggerSource"] == "scheduled_job"

    asyncio.run(run_case())


def test_scheduled_run_rerun_is_forbidden(isolated_db, tmp_path, monkeypatch):
    project = _prepare_project(tmp_path)
    _patch_fake_agent(monkeypatch, _empty_execute)

    async def run_case():
        chat_run_id = await start_scheduled_chat_run(
            _request(project_id=project["id"], firing_id="firing-rerun"),
        )
        status = await await_scheduled_chat_run(chat_run_id, timeout_seconds=2)
        assert status == "completed"
        with pytest.raises(HTTPException) as exc:
            await rerun_run(chat_run_id, project_id=project["id"])
        assert exc.value.status_code == 409
        assert "run-now" in str(exc.value.detail)

        background_runs_module._runs.clear()
        with pytest.raises(HTTPException) as persisted_exc:
            await rerun_run(chat_run_id, project_id=project["id"])
        assert persisted_exc.value.status_code == 409
        restored = await get_chat_background_run(chat_run_id)
        assert restored is not None
        assert restored.trigger_source == "scheduled_job"
        assert restored.scheduled_job_id == "job-bridge1"
        assert restored.scheduled_firing_id == "firing-rerun"

    asyncio.run(run_case())


def test_start_scheduled_chat_run_uses_domain_error_not_http(isolated_db):
    ensure_db()

    async def run_case():
        with pytest.raises(ScheduledChatLaunchError, match="frozen_prompt"):
            await start_scheduled_chat_run(_request(frozen_prompt="   "))
        with pytest.raises(ScheduledChatLaunchError, match="Project"):
            await start_scheduled_chat_run(_request(project_id="missing-project"))

    asyncio.run(run_case())
