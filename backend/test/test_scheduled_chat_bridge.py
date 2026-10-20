"""start_scheduled_chat_run 领域 API：立即返回、等待/超时、禁止 rerun。"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from src.scheduler.models import JobExecutionPolicy
from fastapi import HTTPException
from pydantic import ValidationError

from test.snapshot_support import freeze_test_snapshot
from src.agents.factory import AgentGuardrails, AgentModelOptions
from src.agents.runtime_types import RuntimeToolScope
from src.config.runtime_paths import session_job_firing_root
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
from src.server.routes.chat.run_context import ChatRunContext, resolve_run_workspace_dir
from src.server.routes.chat.runtime_builder import RuntimeModelConfig
from src.storage import database
from src.storage.database import (
    DEFAULT_PROJECT_ID,
    DEFAULT_WORKSPACE_ID,
    create_project,
    ensure_db,
    get_chat_run,
    get_chat_session,
    list_chat_sessions,
)


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    """每个用例独立 SQLite，并收口后台 Chat Run。"""

    monkeypatch.setattr("src.config.runtime_paths.USER_DATA_ROOT", tmp_path / "user-data")
    yield
    for run in list(background_runs_module._runs.values()):
        if run.async_task is not None and not run.async_task.done():
            run.async_task.cancel()
    background_runs_module._runs.clear()
    database.forget_db(database.db_path())


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


def _bind_owner(project_id: str, job_id: str = "job-bridge1") -> str:
    """建立 owner Session 與 Job 列，firing 不得再另開 sched-* Session。"""

    from src.sessions.jobs import assign_job_owner
    from src.sessions.records import create_session

    owner = create_session(
        title="Job owner",
        kind="job-owner",
        project_id=project_id,
    )["id"]
    now = "2026-08-21T00:00:00+00:00"
    database.insert_scheduled_job({
        "id": job_id,
        "name": f"周报-{job_id}",
        "status": "active",
        "agent_id": "default",
        "project_id": project_id,
        "schedule_kind": "weekly",
        "schedule_expr": {"weekdays": ["fri"], "at": "18:00"},
        "timezone": "Asia/Shanghai",
        "frozen_prompt": "生成本周周报",
        "created_from_session_id": owner,
        "created_at": now,
        "updated_at": now,
    })
    return assign_job_owner(job_id, owner, title="周报")


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
        "execution_policy": JobExecutionPolicy(),
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


def _patch_fake_agent(monkeypatch, execute, *, filesystem_scope: str):
    agent = SimpleNamespace(
        name="Assistant",
        guardrails=SimpleNamespace(
            filesystem_scope=filesystem_scope,
            shell_blacklist=[],
            network_enabled=True,
            human_approval=False,
            tool_permission_mode="accept_edits",
            tool_permission_profile="confirm_destructive",
            execution_backend="local",
            docker_image="python:3.11-slim",
            persistent_sandbox=False,
            timeout_seconds=300,
        ),
        skills=[],
    )
    passed: dict[str, str | None] = {"session_workspace_dir": None}

    async def fake_build(job, firing, *, run_data, runtime_context, session_workspace_dir=None):
        _ = job, firing
        passed["session_workspace_dir"] = session_workspace_dir
        ctx = _fake_context(run_data, runtime_context)
        if session_workspace_dir:
            ctx.session_workspace_dir = str(session_workspace_dir)
            ctx.write_scope_dir = str(session_workspace_dir)
        ctx.trigger_source = "scheduled_job"
        return ctx

    monkeypatch.setattr("src.scheduler.chat_bridge.get_registered_agent", lambda agent_id: agent)
    monkeypatch.setattr("src.server.routes.chat.scheduled_launch.build_scheduled_run_context", fake_build)
    monkeypatch.setattr("src.server.routes.chat.scheduled_launch.execute_chat_runtime", execute)
    monkeypatch.setattr("src.server.routes.chat.scheduled_launch.get_startup_error", lambda ctx, data: None)
    monkeypatch.setattr(
        "src.server.routes.chat.handlers.send.start_chat_run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("禁止直接调 start_chat_run")),
    )
    return passed


async def _empty_execute(*, ctx, run_data):
    _ = ctx, run_data
    if False:
        yield


def test_chat_message_forbids_trigger_source():
    """公开 ChatMessage 不得出现 trigger_source。"""

    with pytest.raises(ValidationError):
        ChatMessage(message="周报", trigger_source="scheduled_job")


def test_project_job_run_directory_layout(tmp_path):
    from src.config.runtime_paths import tenant_root

    directory = project_job_run_directory(tmp_path, "job-abc", "firing-xyz")
    assert directory == tenant_root() / "jobs" / "job-abc" / "runs" / "firing-xyz"
    assert directory.is_dir()
    assert not (tmp_path / ".agent-workstation").exists()
    with pytest.raises(ValueError, match="Job ID"):
        project_job_run_directory(tmp_path, "../escape", "firing-xyz")


def test_resolve_run_workspace_dir_override_skips_session_cwd(tmp_path):
    job_dir = tmp_path / ".agent-workstation" / "jobs" / "job-1" / "runs" / "firing-1"
    job_dir.mkdir(parents=True)
    resolved = resolve_run_workspace_dir(
        filesystem_scope=tmp_path,
        session_id="sched-session",
        session_workspace_dir=str(job_dir),
    )
    assert Path(resolved) == job_dir.resolve()
    assert not (tmp_path / ".agent-workstation" / "sessions" / "sched-session").exists()
    session_dir = resolve_run_workspace_dir(
        filesystem_scope=tmp_path,
        session_id="sched-session",
    )
    from src.config.runtime_paths import session_root

    assert Path(session_dir) == session_root("sched-session")
    assert ".agent-workstation" not in Path(session_dir).parts


def test_scheduler_package_does_not_eagerly_export_chat_bridge():
    import src.scheduler as scheduler_pkg

    assert "start_scheduled_chat_run" not in scheduler_pkg.__all__
    assert not hasattr(scheduler_pkg, "start_scheduled_chat_run")


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

    passed = _patch_fake_agent(
        monkeypatch, hanging_execute, filesystem_scope=project["rootPath"],
    )

    async def run_case():
        req = _request(project_id=project["id"])
        owner = _bind_owner(project["id"], req.job_id)
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
        session = get_chat_session(live.session_id)
        assert session is not None and session["kind"] == "job-owner"
        assert live.session_id == owner
        workspace = session_job_firing_root(owner, req.job_id, req.firing_id)
        assert (workspace / "spec.json").is_file()
        assert (workspace / "outputs").is_dir()
        session_root = Path(project["rootPath"]) / ".agent-workstation" / "sessions"
        assert not session_root.exists() or not any(session_root.iterdir())
        release.set()
        status = await await_scheduled_chat_run(chat_run_id, timeout_seconds=2)
        assert status == "completed"
        persisted = get_chat_run(chat_run_id)
        assert persisted is not None
        assert persisted["status"] == "completed"
        assert persisted["triggerSource"] == "scheduled_job"
        assert persisted["scheduledJobId"] == req.job_id
        assert persisted["scheduledFiringId"] == req.firing_id
        session_after = get_chat_session(live.session_id)
        assert session_after is not None and session_after["kind"] == "job-owner"
        manifest = scan_scheduled_run_outputs(
            workspace, session_id=owner, run_id=req.firing_id,
        )
        assert [item["path"] for item in manifest["files"]] == ["outputs/weekly-report.md"]
        from src.sessions.files import list_session_artifacts

        artifacts = list_session_artifacts(owner)
        assert any(item["runId"] == req.firing_id for item in artifacts)
        runs = database.list_chat_runs(session_id=owner)
        assert chat_run_id in {item["id"] for item in runs}
        traced = get_chat_run(chat_run_id)
        assert traced is not None and traced["sessionId"] == owner
        from src.scheduler.models import ScheduledJobRun
        from src.scheduler.notifications import notify_firing_terminal
        from src.scheduler.service import ScheduledJobService
        from src.storage.database import list_user_notifications

        note = notify_firing_terminal(
            ScheduledJobService().get_job(req.job_id),
            ScheduledJobRun(
                id=req.firing_id,
                job_id=req.job_id,
                workspace_id=req.workspace_id,
                project_id=req.project_id,
                trigger="schedule",
                scheduled_for=req.scheduled_for,
                status="succeeded",
                chat_session_id=owner,
                chat_run_id=chat_run_id,
                created_at="2026-08-21T10:00:00+00:00",
                updated_at="2026-08-21T10:00:00+00:00",
            ),
        )
        assert note is not None
        visible = [
            item for item in list_user_notifications()
            if item.get("owner_session_id") == owner
        ]
        assert visible and visible[0]["job_id"] == req.job_id
        assert passed["session_workspace_dir"] == str(workspace)
        return live.session_id

    session_id = asyncio.run(run_case())
    assert not session_id.startswith("sched-")


def test_each_firing_creates_a_new_scheduled_session(isolated_db, tmp_path, monkeypatch):
    project = _prepare_project(tmp_path)
    owner = _bind_owner(project["id"])
    _patch_fake_agent(monkeypatch, _empty_execute, filesystem_scope=project["rootPath"])

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
        assert left.session_id == right.session_id == owner
        assert first != second
        assert not any(item["id"].startswith("sched-") for item in list_chat_sessions())

    asyncio.run(run_case())


def test_await_timeout_cancels_chat_background_run(isolated_db, tmp_path, monkeypatch):
    project = _prepare_project(tmp_path)
    _bind_owner(project["id"])

    async def never_finish(*, ctx, run_data):
        _ = ctx, run_data
        await asyncio.sleep(30)
        if False:
            yield

    _patch_fake_agent(monkeypatch, never_finish, filesystem_scope=project["rootPath"])

    async def run_case():
        chat_run_id = await start_scheduled_chat_run(
            _request(project_id=project["id"], firing_id="firing-timeout"),
        )
        live = await get_chat_background_run(chat_run_id)
        task = live.async_task if live is not None else None
        status = await await_scheduled_chat_run(chat_run_id, timeout_seconds=0.2)
        assert status == "cancelled"
        if task is not None:
            assert task.done()
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
    _bind_owner(project["id"])
    _patch_fake_agent(monkeypatch, _empty_execute, filesystem_scope=project["rootPath"])

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
    _bind_owner(DEFAULT_PROJECT_ID)

    async def run_case():
        with pytest.raises(ScheduledChatLaunchError, match="frozen_prompt"):
            await start_scheduled_chat_run(_request(frozen_prompt="   "))
        started = await start_scheduled_chat_run(_request(project_id="missing-project"))
        assert started

    asyncio.run(run_case())


def test_job_cwd_readonly_allowed_root_fails_launch(isolated_db, tmp_path, monkeypatch):
    project = _prepare_project(tmp_path)
    owner = _bind_owner(project["id"])
    from src.config.runtime_paths import session_root

    root = session_root(owner)
    root.mkdir(parents=True, exist_ok=True)
    database.ensure_db()
    with database._lock, database._connect() as conn:
        conn.execute(
            """
            INSERT INTO session_allowed_roots (
                session_id, normalized_path, access_mode, source, created_at
            ) VALUES (?, ?, 'read', 'user', '2026-08-21T00:00:00+00:00')
            """,
            (owner, str(root.resolve())),
        )
        conn.commit()
    _patch_fake_agent(monkeypatch, _empty_execute, filesystem_scope=str(project["rootPath"]))

    async def run_case():
        with pytest.raises(ScheduledChatLaunchError, match="只讀 allowed root"):
            await start_scheduled_chat_run(
                _request(project_id=project["id"], firing_id="firing-scope"),
            )

    asyncio.run(run_case())


def test_timeout_joins_cancelled_task_before_returning(isolated_db, tmp_path, monkeypatch):
    project = _prepare_project(tmp_path)
    _bind_owner(project["id"])
    unwound = asyncio.Event()

    async def slow_unwind(*, ctx, run_data):
        _ = ctx, run_data
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            await asyncio.shield(asyncio.sleep(0.05))
            unwound.set()
            raise
        if False:
            yield

    _patch_fake_agent(monkeypatch, slow_unwind, filesystem_scope=project["rootPath"])

    async def run_case():
        chat_run_id = await start_scheduled_chat_run(
            _request(project_id=project["id"], firing_id="firing-join"),
        )
        status = await await_scheduled_chat_run(chat_run_id, timeout_seconds=0.2)
        assert status == "cancelled"
        assert unwound.is_set()

    asyncio.run(run_case())


def test_daily_fresh_firings_skip_chat_timeline_and_prior_history(isolated_db, tmp_path):
    """同一 owner 連續兩天 firing：不寫 Chat 主訊息，也不繼承前次或對話全文。"""

    from src.runtime.agent_spec import build_main_agent_runtime_spec
    from src.server.routes.chat.completion import finalize_successful_leader_run
    from src.server.routes.chat.initial_events import persist_user_message_and_trace
    from src.server.routes.chat.models import ChatToolRun
    from src.server.routes.chat.run_context import snapshot_session_history
    from src.storage.database import list_chat_messages, list_run_events

    project = _prepare_project(tmp_path)
    owner = _bind_owner(project["id"])
    now = "2026-08-20T00:00:00+00:00"
    database.append_chat_message(owner, {
        "id": "msg-user",
        "role": "user",
        "content": "昨天的完整对话",
        "created_at": now,
    })
    database.append_chat_message(owner, {
        "id": "msg-assistant",
        "role": "assistant",
        "content": "昨天的完整回答与工具历史",
        "created_at": now,
    })
    assert snapshot_session_history(owner)

    prompts = ("第一天只写日报", "第二天只写日报")
    keys: list[tuple[str, str]] = []
    for index, prompt in enumerate(prompts, start=1):
        message = ChatMessage(
            session_id=owner,
            project_id=project["id"],
            message=prompt,
            agent_id="default",
        )
        ctx = _fake_context(message, SimpleNamespace(
            workspace_id=DEFAULT_WORKSPACE_ID,
            project_id=project["id"],
            session_id=owner,
            run_id=f"run-day-{index}",
        ))
        ctx.trigger_source = "scheduled_job"
        ctx.scheduled_firing_id = f"firing-day-{index}"
        ctx.persist_run = True
        ctx.session_history = []
        persist_user_message_and_trace(ctx, message)
        assert ctx.session_history == []
        spec = build_main_agent_runtime_spec(
            agent_id="default",
            agent_name="Assistant",
            session_id=owner,
            system_prompt="prompt",
            model_config=ctx.runtime_model_config,
            deep_thinking=False,
            used_skills=[],
            guardrails=AgentGuardrails(),
            mcp_servers=[],
            knowledge_tools=[],
            runtime_tool_scope=_frozen_scope(),
            guardrail_cache_key="guard",
            context_scope=f"firing:{ctx.scheduled_firing_id}",
            run_snapshot=freeze_test_snapshot(owner),
            apply_snapshot_filesystem=False,
        )
        keys.append((spec.runtime_agent_id, spec.session_context_key))

    contents = [item["content"] for item in list_chat_messages(owner)]
    assert contents == ["昨天的完整对话", "昨天的完整回答与工具历史"]
    assert keys[0] != keys[1]
    assert "firing:firing-day-1" in keys[0][1]
    assert "firing:firing-day-2" in keys[1][1]
    chat_spec = build_main_agent_runtime_spec(
        agent_id="default",
        agent_name="Assistant",
        session_id=owner,
        system_prompt="prompt",
        model_config=RuntimeModelConfig(
            model_name="model",
            fallback_names=[],
            model_options=AgentModelOptions(max_react_iters=40),
        ),
        deep_thinking=False,
        used_skills=[],
        guardrails=AgentGuardrails(),
        mcp_servers=[],
        knowledge_tools=[],
        runtime_tool_scope=_frozen_scope(),
        guardrail_cache_key="guard",
        run_snapshot=freeze_test_snapshot(owner, agent_id="default"),
        apply_snapshot_filesystem=False,
    )
    assert chat_spec.session_context_key not in {keys[0][1], keys[1][1]}

    async def finish() -> None:
        async for _event in finalize_successful_leader_run(
            session_id=owner,
            persist=True,
            agent_id="default",
            agent_name="Assistant",
            model_name="model",
            user_message=prompts[1],
            assistant_text="第二天结果",
            assistant_thinking="",
            assistant_tool_calls=[ChatToolRun(id="tool-1", name="Write", args="{}", result="ok", status="completed")],
            assistant_model_caches=[],
            runtime_events=[],
            skill_runs=[],
            active_skill_run=None,
            record_chat_message=False,
        ):
            pass

    asyncio.run(finish())
    assert "第二天结果" not in [item["content"] for item in list_chat_messages(owner)]
    events = list_run_events(owner)
    assert any(item["eventType"] == "USER_MESSAGE" and "第一天只写日报" in item["content"] for item in events)
    assert any(item["eventType"] == "USER_MESSAGE" and "第二天只写日报" in item["content"] for item in events)
    assistant = [item for item in events if item["eventType"] == "ASSISTANT_MESSAGE"]
    assert assistant and assistant[-1]["payload"]["toolCalls"][0]["name"] == "Write"
    assert "昨天的完整回答" not in assistant[-1]["content"]
