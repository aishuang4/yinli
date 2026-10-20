"""全新 tenant 的生產調用鏈驗收。先 strict xfail，由對應任務刪除標記。"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.config.runtime_paths import session_artifacts_root
from src.server.main import app
from src.session_migration.legacy_fields import LEGACY_SCOPE_FIELDS, LEGACY_SESSION_COLUMNS
from src.sessions.records import create_session, get_run_snapshot
from src.sessions.resources import put_session_resources
from src.sessions.run_access import candidate_resource_access, run_resource_access
from src.sessions.run_binding import begin_run
from src.storage import database
pytest_plugins = ["test.fresh_tenant", "test.hosted_support"]

from test.fresh_tenant import (
    capture_model_boundary,
    fake_model_binding,
    install_skill,
    register_agent,
    wait_run,
    write_http_tool,
    write_mcp,
)


def _stable_id(item: object) -> str:
    raw = str(getattr(item, "id", "") or getattr(item, "name", "") or "")
    return raw.replace("_", "-")


def _scope_columns_blank(session_id: str) -> None:
    with database._lock, database._connect() as conn:
        row = conn.execute("SELECT * FROM chat_sessions WHERE id = ?", (session_id,)).fetchone()
    assert row is not None
    assert row[LEGACY_SESSION_COLUMNS[0]] == ""
    assert row[LEGACY_SESSION_COLUMNS[1]] == ""


def _run_count(session_id: str) -> int:
    with database._lock, database._connect() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM chat_runs WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0]


def _snapshot_count(run_id: str) -> int:
    with database._lock, database._connect() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM run_context_snapshots WHERE run_id = ?",
            (run_id,),
        ).fetchone()[0]


def test_session_without_grants_keeps_agent_resources(fresh_tenant) -> None:
    skill_id = install_skill("agent-only-skill", shared=False)
    write_mcp("mcp-only")
    write_http_tool("http-only")
    register_agent("agent-resources", skills=(skill_id,), mcp=("mcp-only",), http=("http-only",))
    session = create_session(title="無 grant", agent_id="agent-resources")
    preview = candidate_resource_access(session["id"], agent_id="agent-resources", permission_mode="manual")
    begin_run(
        session["id"],
        "run-agent-resources",
        agent_id="agent-resources",
        permission_mode="manual",
        start_model=lambda item: item,
    )
    frozen = run_resource_access("run-agent-resources")
    assert preview == frozen
    assert skill_id in frozen.skill_ids
    assert "mcp-only" in frozen.mcp_server_ids
    assert "http-only" in frozen.http_tool_ids


def test_fresh_desktop_first_message_reaches_model_boundary(fresh_tenant, monkeypatch) -> None:
    skill_id = install_skill("desktop-skill", shared=False)
    write_mcp("mcp-desktop")
    write_http_tool("http-desktop")
    register_agent("agent-desktop", skills=(skill_id,), mcp=("mcp-desktop",), http=("http-desktop",))
    calls = capture_model_boundary(monkeypatch)
    fake_model_binding(monkeypatch)
    client = TestClient(app)
    created = client.post("/api/sessions", json={"title": "第一條", "agentId": "agent-desktop"})
    assert created.status_code == 201, created.text
    session_id = created.json()["id"]
    started = client.post("/api/chat/send/start", json={
        "message": "你好",
        "session_id": session_id,
        "agent_id": "agent-desktop",
    })
    assert started.status_code == 200, started.text
    run_id = started.json()["runId"] if "runId" in started.json() else started.json().get("id") or started.json()["run"]["id"]
    wait_run(run_id)
    assert _snapshot_count(run_id) == 1
    assert calls, "模型邊界未被呼叫"
    kwargs = calls[0]["kwargs"]
    assert kwargs.get("run_snapshot")
    assert Path(kwargs["workspace_dir"]).resolve() == session_artifacts_root(session_id).resolve()
    skill_paths = [str(path) for path in (kwargs.get("skill_paths") or [])]
    assert any(skill_id in path or path.endswith("desktop-skill") or "desktop-skill" in path for path in skill_paths)
    mcp_ids = {_stable_id(item) for item in (kwargs.get("mcp_servers") or [])}
    http_ids = {_stable_id(item) for item in (kwargs.get("knowledge_tools") or [])}
    assert "mcp-desktop" in mcp_ids
    assert "http-desktop" in http_ids
    _scope_columns_blank(session_id)


def test_fresh_hosted_first_message_reaches_model_boundary(hosted_env, monkeypatch) -> None:
    from src.tenancy.context import use_tenant
    from test.fresh_tenant import capture_model_boundary, fake_model_binding, install_skill, register_agent, write_http_tool, write_mcp
    from test.hosted_support import make_user, tenant_of

    calls = capture_model_boundary(monkeypatch)
    fake_model_binding(monkeypatch)
    client = TestClient(app, client=("127.0.0.1", 50000), base_url="https://testserver")
    token, user_id = make_user(client, "fresh-hosted@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    with use_tenant(tenant_of(user_id)):
        skill_id = install_skill("hosted-skill", shared=False)
        write_mcp("mcp-hosted")
        write_http_tool("http-hosted")
        register_agent("agent-hosted", skills=(skill_id,), mcp=("mcp-hosted",), http=("http-hosted",))
    created = client.post("/api/sessions", json={"title": "託管第一條", "agentId": "agent-hosted"}, headers=headers)
    assert created.status_code == 201, created.text
    session_id = created.json()["id"]
    started = client.post("/api/chat/send/start", json={
        "message": "你好",
        "session_id": session_id,
        "agent_id": "agent-hosted",
    }, headers=headers)
    assert started.status_code == 200, started.text
    body = started.json()
    run_id = body.get("runId") or body.get("id") or body["run"]["id"]
    wait_run(run_id)
    kwargs = calls[0]["kwargs"]
    assert kwargs.get("run_snapshot")
    with use_tenant(tenant_of(user_id)):
        assert _snapshot_count(run_id) == 1
        assert Path(kwargs["workspace_dir"]).resolve() == session_artifacts_root(session_id).resolve()
        _scope_columns_blank(session_id)


def test_missing_working_directory_fails_closed(fresh_tenant, monkeypatch) -> None:
    work = fresh_tenant / "gone"
    work.mkdir()
    session = create_session(title="目錄消失", working_directory=str(work), agent_id="default")
    work.rmdir()
    calls = capture_model_boundary(monkeypatch)
    fake_model_binding(monkeypatch)
    client = TestClient(app)
    started = client.post("/api/chat/send/start", json={
        "message": "你好",
        "session_id": session["id"],
        "agent_id": "default",
    })
    assert started.status_code == 422
    assert started.json()["detail"]["code"] == "directory_unavailable"
    assert _run_count(session["id"]) == 0
    with database._lock, database._connect() as conn:
        snapshots = conn.execute("SELECT COUNT(*) FROM run_context_snapshots").fetchone()[0]
    assert snapshots == 0
    assert calls == []


def test_capability_reader_follows_run_snapshot(fresh_tenant) -> None:
    from src.agents.runtime_types import RuntimeToolScope
    from src.runtime.authorization_resources import RuntimeCapabilityReader
    from types import SimpleNamespace

    write_mcp("mcp-snap")
    register_agent("agent-snap", mcp=("mcp-snap",))
    session = create_session(title="快照覆核", agent_id="agent-snap")
    put_session_resources(session["id"], [{
        "resourceType": "mcp",
        "resourceId": "mcp-snap",
        "enabled": True,
        "configOverride": {},
    }])
    begin_run(session["id"], "run-cap", agent_id="agent-snap", permission_mode="manual", start_model=lambda item: item)
    reader = RuntimeCapabilityReader(
        context=SimpleNamespace(session_id=session["id"], run_id="run-cap"),
        actor_id="agent-snap",
        tool_scope=RuntimeToolScope(),
    )
    _version, _guardrails, mcp, _knowledge = reader.snapshot()
    assert any(_stable_id(item) == "mcp-snap" for item in mcp)
    put_session_resources(session["id"], [])
    _version_after, _guardrails_after, mcp_after, _knowledge_after = reader.snapshot()
    assert any(_stable_id(item) == "mcp-snap" for item in mcp_after)
    text = (runtime_paths_mcp_disable())
    del text
    from src.catalog.mcp_files import discover_mcp_files

    disabled = runtime_paths_disable_mcp()
    del disabled
    version_disabled, _, mcp_disabled, _ = reader.snapshot()
    assert version_disabled != _version
    assert all(_stable_id(item) != "mcp-snap" for item in mcp_disabled)


def runtime_paths_mcp_disable():
    return None


def runtime_paths_disable_mcp() -> None:
    from src.catalog.mcp_files import McpFileRecord, dump_mcp_yaml
    from src.config.runtime_paths import global_mcp_root

    record = McpFileRecord(id="mcp-snap", name="mcp-snap", transport="stdio", command="true", enabled=False)
    (global_mcp_root() / "mcp-snap.yaml").write_text(dump_mcp_yaml(record), encoding="utf-8")


def test_snapshot_failure_creates_no_run(fresh_tenant, monkeypatch) -> None:
    calls = capture_model_boundary(monkeypatch)
    fake_model_binding(monkeypatch)
    session = create_session(title="缺 Agent", agent_id="missing-agent")
    client = TestClient(app)
    started = client.post("/api/chat/send/start", json={
        "message": "你好",
        "session_id": session["id"],
        "agent_id": "missing-agent",
    })
    assert started.status_code == 409
    assert started.json()["detail"]["code"] == "resource_not_found"
    assert _run_count(session["id"]) == 0
    assert calls == []


def test_main_spec_requires_snapshot() -> None:
    from src.agents.runtime_types import AgentGuardrails, AgentModelOptions, RuntimeToolScope
    from src.runtime.agent_spec import RuntimeModelConfig, build_main_agent_runtime_spec
    from src.sessions.errors import SessionError

    with pytest.raises(SessionError) as exc_info:
        build_main_agent_runtime_spec(
            agent_id="default",
            agent_name="助手",
            session_id="ses",
            system_prompt="prompt",
            model_config=RuntimeModelConfig(
                model_name="demo",
                fallback_names=[],
                model_options=AgentModelOptions(),
            ),
            deep_thinking=False,
            used_skills=[],
            guardrails=AgentGuardrails(),
            mcp_servers=[],
            knowledge_tools=[],
            runtime_tool_scope=RuntimeToolScope(),
            guardrail_cache_key="none",
            run_snapshot=None,
        )
    assert exc_info.value.code == "snapshot_required"


def test_subagent_requires_parent_snapshot() -> None:
    from src.sessions.errors import SessionError
    from src.subagents.models import SubagentDefinition
    from src.subagents.runtime_context import build_subagent_runtime_context

    definition = SubagentDefinition(
        name="child",
        description="desc",
        prompt="prompt",
        scope="chat",
        path="child.md",
    )
    with pytest.raises(SessionError) as exc_info:
        build_subagent_runtime_context(definition, parent_snapshot=None)
    assert exc_info.value.code == "snapshot_required"


def test_fresh_job_confirm_enable_fire_once(fresh_tenant, monkeypatch) -> None:
    import asyncio

    from src.scheduler.chat_bridge import ScheduledRunRequest, start_scheduled_chat_run
    from src.scheduler.models import JobExecutionPolicy
    from src.scheduler.service import ScheduledJobService
    from src.scheduler.settings import set_scheduled_jobs_settings
    from src.agents.runtime_types import RuntimeToolScope

    calls = capture_model_boundary(monkeypatch)
    fake_model_binding(monkeypatch)
    set_scheduled_jobs_settings(enabled=True, capability_gate=False)
    session = create_session(title="Job owner", kind="job-owner", agent_id="system-websearch-job")
    job = ScheduledJobService().create_job(
        **{LEGACY_SESSION_COLUMNS[0]: "", LEGACY_SESSION_COLUMNS[1]: ""},
        name="新任務",
        agent_id="system-websearch-job",
        schedule_kind="weekly",
        schedule_expr={"weekdays": ["fri"], "at": "18:00"},
        timezone="Asia/Shanghai",
        frozen_prompt="寫周報",
        created_from_session_id=session["id"],
    )
    confirmed = ScheduledJobService().confirm(job.id)
    assert confirmed.status == "active"
    request = ScheduledRunRequest(**{
        "job_id": job.id,
        "firing_id": "firing-fresh",
        LEGACY_SESSION_COLUMNS[0]: "",
        LEGACY_SESSION_COLUMNS[1]: "",
        "agent_id": job.agent_id,
        "frozen_prompt": job.frozen_prompt,
        "skill_ids": [],
        "tool_scope": RuntimeToolScope(),
        "execution_policy": JobExecutionPolicy(),
        "scheduled_for": "2026-10-20T10:00:00+00:00",
        "trigger": "manual",
    })
    try:
        run_id = asyncio.run(start_scheduled_chat_run(request))
    except Exception as exc:
        assert "project_archived" not in str(exc)
        raise
    wait_run(run_id)
    assert get_run_snapshot(run_id) is not None
    assert calls


def test_fresh_group_studio_create_and_one_worker_run(fresh_tenant, monkeypatch) -> None:
    calls = capture_model_boundary(monkeypatch)
    fake_model_binding(monkeypatch)
    monkeypatch.setattr("src.group_studio.scheduler.should_auto_run", lambda: True)
    register_agent("agent-room", skills=(), mcp=(), http=())
    client = TestClient(app)
    created = client.post("/api/sessions", json={"title": "群聊", "agentId": "agent-room"})
    assert created.status_code == 201, created.text
    session_id = created.json()["id"]
    payload = {"chatSessionId": session_id, "agentId": "agent-room", "draftText": "開始"}
    assert LEGACY_SCOPE_FIELDS.isdisjoint(payload)
    studio = client.post("/api/group-studio/from-chat", json=payload)
    assert studio.status_code == 200, studio.text
    group_id = studio.json()["id"]
    posted = client.post(f"/api/group-studio/{group_id}/messages", json={"body": "請發言", "mentions": []})
    assert posted.status_code == 200, posted.text
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not calls:
        client.get(f"/api/group-studio/{group_id}")
        time.sleep(0.05)
    assert calls
    assert any(get_run_snapshot(run_id) is not None for run_id in _recent_run_ids())


def _recent_run_ids() -> list[str]:
    with database._lock, database._connect() as conn:
        rows = conn.execute("SELECT run_id FROM run_context_snapshots").fetchall()
    return [row[0] for row in rows]


def test_fresh_tenant_startup_gate_cuts_over(fresh_tenant) -> None:
    from src.session_migration.runner import ensure_tenant_cutover
    from src.session_migration.state import cutover_state, migration_dir

    result = ensure_tenant_cutover()
    assert result == {"status": "fresh_cutover"}
    assert cutover_state()["cutover"] == "cutover"
    assert (migration_dir() / "complete.json").is_file()
