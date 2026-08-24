"""无人值守 firing：冻结 ToolScope/Skill、工具裁剪与 cwd Write overlay。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.agents.factory import AgentGuardrails, AgentModelOptions, _create_default_toolkit
from src.agents.runtime_types import RuntimeToolScope
from src.project_files import project_job_run_directory
from src.scheduler.models import JobToolScope, ScheduledJobValidationError
from src.scheduler.service import ScheduledJobService
from src.scheduler.unattended_policy import would_fail_unattended_tools
from src.server.routes.chat.hitl import PENDING_INTERACTIONS, build_automatic_confirm_resume
from src.server.routes.chat.hitl_unattended import (
    MAX_UNATTENDED_TOOL_DENIALS,
    hitl_unattended_resolve,
)
from src.server.routes.chat.leader_stream import LeaderStreamState, stream_leader_agent_reply
from src.server.routes.chat.models import ChatMessage
from src.server.routes.chat.react_runtime import _build_runtime_feature_parts
from src.server.routes.chat.run_context import build_scheduled_run_context
from src.server.routes.chat.scheduled_runtime import (
    SCHEDULED_JOB_UNATTENDED_DENYLIST,
    deserialize_frozen_runtime_tool_scope,
    scheduled_job_unattended_feature_key,
)
from src.server.routes.chat.team_runtime.member_runtime import _build_team_tool_denial
from src.server.routes.skills import Skill
from src.storage import database
from src.storage.database import (
    DEFAULT_PROJECT_ID,
    DEFAULT_WORKSPACE_ID,
    create_project,
    ensure_db,
)


class _FakeAgentScopeEvent:
    def __init__(self, payload: dict):
        self.payload = payload

    def model_dump(self, mode: str = "json") -> dict:
        return dict(self.payload)


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "agent_workstation.db")
    monkeypatch.setattr(database, "_initialized", False)
    yield
    database._initialized = False


def _service() -> ScheduledJobService:
    ensure_db()
    return ScheduledJobService()


def _weekly_job(service: ScheduledJobService, **overrides):
    payload = {
        "workspace_id": DEFAULT_WORKSPACE_ID,
        "project_id": DEFAULT_PROJECT_ID,
        "name": "周五周报",
        "agent_id": "agent-1",
        "schedule_kind": "weekly",
        "schedule_expr": {"weekdays": ["fri"], "at": "18:00"},
        "timezone": "Asia/Shanghai",
        "frozen_prompt": "生成本周周报，写入 outputs/weekly-report.md",
    }
    payload.update(overrides)
    return service.create_job(**payload)


def _job_ns(**overrides):
    payload = {
        "id": "job-1",
        "agent_id": "default",
        "frozen_prompt": "生成本周周报，写入 outputs/weekly-report.md",
        "skill_ids": [],
        "tool_scope": JobToolScope(),
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


def _firing_ns(**overrides):
    payload = {"id": "firing-1", "job_id": "job-1"}
    payload.update(overrides)
    return SimpleNamespace(**payload)


def _agent_ctx(*, trigger_source: str, project_root: Path):
    return SimpleNamespace(
        session_id="session-1",
        agent_id="agent-1",
        agent_name="Agent",
        persist_run=True,
        trigger_source=trigger_source,
        tool_name_denylist=set(SCHEDULED_JOB_UNATTENDED_DENYLIST) if trigger_source == "scheduled_job" else set(),
        intent_route=SimpleNamespace(executor=SimpleNamespace(kind="single_agent")),
        runtime_context=SimpleNamespace(project_root=project_root),
        runtime_model_config=SimpleNamespace(
            model_name="model",
            fallback_names=[],
            model_options=AgentModelOptions(),
        ),
        guardrails=AgentGuardrails(),
        mcp_servers=[],
        knowledge_tools=[],
        runtime_tool_scope=RuntimeToolScope(
            allow_mcp=False,
            allow_knowledge=False,
            allow_bash=False,
            allow_http=False,
            allow_web=False,
        ),
    )


def _toolkit_tools(toolkit):
    return [tool for group in toolkit.tool_groups for tool in group.tools]


async def _tool_names(toolkit) -> list[str]:
    schemas = await toolkit.get_tool_schemas()
    return [
        item.get("function", {}).get("name") or item.get("name")
        for item in schemas
    ]


def test_deserialize_frozen_tool_scope_missing_keys_are_false():
    """缺省键视为 false，禁止落入 RuntimeToolScope 全 True。"""

    frozen = deserialize_frozen_runtime_tool_scope({})
    assert frozen.allow_bash is False
    assert frozen.allow_http is False
    assert frozen.allow_web is False
    assert frozen.allow_mcp is False
    assert frozen.allow_knowledge is False
    runtime_default = RuntimeToolScope()
    assert runtime_default.allow_bash is True
    assert deserialize_frozen_runtime_tool_scope(None).allow_mcp is False
    assert deserialize_frozen_runtime_tool_scope({"allowBash": True}).allow_bash is True
    assert deserialize_frozen_runtime_tool_scope({"allow_http": True}).allow_web is False


def test_feature_key_includes_sorted_denylist_fingerprint():
    key = scheduled_job_unattended_feature_key()
    assert key.startswith("scheduled-job-unattended:")
    assert "AskUser" in key
    assert "DelegateTask" in key
    names = key.split(":", 1)[1].split(",")
    assert names == sorted(names)


def test_same_agent_interactive_then_firing_drops_askuser_and_delegate_task(monkeypatch, tmp_path):
    """同一 agent_id 先交互再 firing：firing toolkit 无 AskUser / DelegateTask。"""

    class _DelegateTaskStub:
        name = "DelegateTask"

    monkeypatch.setattr(
        "src.server.routes.chat.react_runtime.load_subagent_catalog",
        lambda workspace_dir: SimpleNamespace(
            definitions={"researcher": SimpleNamespace(description="研究")},
            fingerprint="fp-researcher",
        ),
    )
    monkeypatch.setattr(
        "src.server.routes.chat.react_runtime.SubagentDelegationManager",
        lambda **kwargs: SimpleNamespace(),
    )
    monkeypatch.setattr(
        "src.server.routes.chat.react_runtime.build_subagent_tools",
        lambda manager: [_DelegateTaskStub()],
    )
    project_root = tmp_path / "project"
    project_root.mkdir()
    interactive_ctx = _agent_ctx(trigger_source="user", project_root=project_root)
    firing_ctx = _agent_ctx(trigger_source="scheduled_job", project_root=project_root)
    interactive_parts = _build_runtime_feature_parts(interactive_ctx, ChatMessage(message="直接回答"))
    firing_parts = _build_runtime_feature_parts(
        firing_ctx, ChatMessage(message="生成本周周报", plan_requested=True),
    )
    workspace = project_root / ".agent-workstation" / "jobs" / "job-1" / "runs" / "firing-1"
    workspace.mkdir(parents=True)
    interactive_toolkit = _create_default_toolkit()
    firing_toolkit = _create_default_toolkit(
        extra_tools=list(firing_parts[2]),
        tool_name_denylist=set(SCHEDULED_JOB_UNATTENDED_DENYLIST),
        tool_scope=RuntimeToolScope(
            allow_mcp=False,
            allow_knowledge=False,
            allow_bash=False,
            allow_http=False,
            allow_web=False,
        ),
        workspace_dir=str(workspace),
        write_scope_dir=str(workspace),
        guardrails=AgentGuardrails(filesystem_scope=str(project_root)),
    )
    interactive_names = asyncio.run(_tool_names(interactive_toolkit))
    firing_names = asyncio.run(_tool_names(firing_toolkit))

    assert "AskUser" in interactive_names
    assert any(getattr(tool, "name", "") == "DelegateTask" for tool in interactive_parts[2])
    assert "AskUser" not in firing_names
    assert "DelegateTask" not in firing_names
    assert "SubagentTaskWait" not in firing_names
    assert firing_parts[0] is None
    assert firing_parts[1].startswith("scheduled-job-unattended:")
    assert firing_parts[2] == []
    write = next(tool for tool in _toolkit_tools(firing_toolkit) if getattr(tool, "name", "") == "Write")
    read = next(tool for tool in _toolkit_tools(firing_toolkit) if getattr(tool, "name", "") == "Read")
    assert Path(write.scope_dir).resolve() == workspace.resolve()
    assert Path(read.scope_dir).resolve() == project_root.resolve()


def test_unattended_write_is_not_team_deny_only(tmp_path):
    """scheduled Leader 批准 cwd Write；Team Member helper 仍是否认。"""

    workspace = tmp_path / "cwd"
    workspace.mkdir()
    data = {
        "reply_id": "reply-1",
        "tool_calls": [{
            "id": "call-write",
            "name": "Write",
            "input": '{"file_path":"outputs/weekly-report.md"}',
            "state": "asking",
        }],
    }
    denied, resume = hitl_unattended_resolve(
        "REQUIRE_USER_CONFIRM", data, workspace_dir=str(workspace),
    )
    team = _build_team_tool_denial("REQUIRE_USER_CONFIRM", data)
    assert denied == []
    assert resume.confirm_results[0].confirmed is True
    assert team.confirm_results[0].confirmed is False
    assert resume.__class__ is team.__class__


def test_unattended_escape_write_is_denied(tmp_path):
    workspace = tmp_path / "cwd"
    workspace.mkdir()
    (tmp_path / "secret.md").write_text("no", encoding="utf-8")
    denied, resume = hitl_unattended_resolve(
        "REQUIRE_USER_CONFIRM",
        {
            "reply_id": "reply-1",
            "tool_calls": [{
                "id": "call-write",
                "name": "Write",
                "input": '{"file_path":"../secret.md"}',
                "state": "asking",
            }],
        },
        workspace_dir=str(workspace),
    )
    assert denied == ["Write"]
    assert resume.confirm_results[0].confirmed is False


def test_ask_every_time_cwd_write_succeeds_on_scheduled_firing(tmp_path):
    """ask_every_time 下 cwd Write 由 overlay 批准，不进入 PENDING_INTERACTIONS。"""

    workspace = tmp_path / "job-cwd"
    (workspace / "outputs").mkdir(parents=True)
    agent = _FakeAskEveryTimeWriteAgent(workspace)
    PENDING_INTERACTIONS.clear()

    async def run_case():
        events = [
            event
            async for event in stream_leader_agent_reply(
                agent=agent,
                user_msg=object(),
                state=LeaderStreamState(
                    trigger_source="scheduled_job",
                    session_workspace_dir=str(workspace),
                ),
                session_id="session-sched-write",
                persist=False,
                agent_id="agent-ask",
                agent_name="Ask Agent",
                model_name="deepseek-chat",
                guardrails=AgentGuardrails(
                    tool_permission_profile="ask_every_time",
                    tool_permission_mode="ask_every_time",
                ),
                message="生成本周周报",
                stream_scope="single_agent",
                skill_runs=[],
            )
        ]
        return events

    events = asyncio.run(run_case())
    try:
        assert PENDING_INTERACTIONS == {}
        assert agent.resumes
        assert agent.resumes[0].confirm_results[0].confirmed is True
        assert (workspace / "outputs" / "weekly-report.md").read_text(encoding="utf-8") == "# 周报\n"
        serialized = json.dumps([event.event_type for event in events], ensure_ascii=False)
        assert "HITL_REQUIRED" not in serialized
        assert "waiting_permission" not in serialized
    finally:
        PENDING_INTERACTIONS.clear()


def test_unattended_deny_limit_fails_without_pending(tmp_path):
    workspace = tmp_path / "cwd"
    workspace.mkdir()
    agent = _FakeAlwaysBashHitlAgent()
    PENDING_INTERACTIONS.clear()
    state = LeaderStreamState(
        trigger_source="scheduled_job",
        session_workspace_dir=str(workspace),
    )

    async def run_case():
        return [
            event
            async for event in stream_leader_agent_reply(
                agent=agent,
                user_msg=object(),
                state=state,
                session_id="session-deny-cap",
                persist=False,
                agent_id="agent-1",
                agent_name="Agent",
                model_name="deepseek-chat",
                guardrails=AgentGuardrails(),
                message="跑脚本",
                stream_scope="single_agent",
                skill_runs=[],
            )
        ]

    asyncio.run(run_case())
    try:
        assert state.stop_reason == "hitl_denied"
        assert state.unattended_denials > MAX_UNATTENDED_TOOL_DENIALS
        assert PENDING_INTERACTIONS == {}
    finally:
        PENDING_INTERACTIONS.clear()


def test_build_scheduled_run_context_skips_model_readiness_and_tool_scope_resolver(
    isolated_db, tmp_path, monkeypatch,
):
    project = _prepare_project(tmp_path)
    calls = {"readiness": 0, "intent": 0, "scope": 0}

    async def boom_readiness(**kwargs):
        calls["readiness"] += 1
        raise AssertionError("不得调用模型 Readiness")

    async def boom_intent(**kwargs):
        calls["intent"] += 1
        raise AssertionError("不得调用模型 intent")

    def boom_scope(**kwargs):
        calls["scope"] += 1
        raise AssertionError("不得调用 tool_scope_resolver")

    monkeypatch.setattr(
        "src.server.routes.chat.run_context.check_message_readiness_with_model",
        boom_readiness,
    )
    monkeypatch.setattr(
        "src.server.routes.chat.run_context.route_chat_message_with_model",
        boom_intent,
    )
    monkeypatch.setattr(
        "src.server.routes.chat.run_context.resolve_react_runtime_tool_scope",
        boom_scope,
    )
    monkeypatch.setattr(
        "src.server.routes.chat.run_context.get_effective_model_settings",
        lambda: SimpleNamespace(default_model="model", fallback_models=[], provider="deepseek", api_key="x"),
    )
    monkeypatch.setattr(
        "src.server.routes.chat.run_context.build_memory_injection_text",
        lambda context: "",
    )
    monkeypatch.setattr(
        "src.server.routes.chat.run_context.get_registered_agent",
        lambda agent_id: SimpleNamespace(
            name="Assistant",
            system_prompt="prompt",
            skills=[],
            model_config_data=None,
            mcp_tools=[],
            knowledge_tools=[],
            guardrails=SimpleNamespace(filesystem_scope=project["rootPath"]),
        ),
    )
    workspace = project_job_run_directory(Path(project["rootPath"]), "job-ctx", "firing-ctx")
    from src.runtime_context import resolve_runtime_context

    runtime_context = resolve_runtime_context(
        project_id=project["id"],
        session_id="sched-ctx",
        run_id="run-ctx",
        agent_id="default",
    )
    ctx = asyncio.run(build_scheduled_run_context(
        _job_ns(id="job-ctx", tool_scope={}),
        _firing_ns(id="firing-ctx", job_id="job-ctx"),
        run_data=ChatMessage(
            session_id="sched-ctx",
            project_id=project["id"],
            message="生成本周周报，写入 outputs/weekly-report.md",
            agent_id="default",
            skill_ids=[],
        ),
        runtime_context=runtime_context,
        session_workspace_dir=str(workspace),
    ))
    assert calls == {"readiness": 0, "intent": 0, "scope": 0}
    assert ctx.trigger_source == "scheduled_job"
    assert ctx.runtime_tool_scope.allow_bash is False
    assert ctx.runtime_tool_scope.allow_mcp is False
    assert ctx.write_scope_dir == str(Path(workspace).resolve())
    assert ctx.used_skills == []
    assert "AskUser" in ctx.tool_name_denylist
    assert ctx.intent_route.executor.kind == "single_agent"
    assert ctx.readiness.source == "scheduled_job"


def test_frozen_skills_must_equal_used_skills_or_skill_unbound(isolated_db, tmp_path, monkeypatch):
    project = _prepare_project(tmp_path)
    installed = {
        "skill-ok": Skill.model_validate({
            "id": "skill-ok",
            "name": "ok",
            "description": "",
            "category": "test",
            "status": "installed",
            "enabled": True,
            "updatedAt": "2026-08-21T00:00:00+00:00",
        }),
    }
    monkeypatch.setattr(
        "src.server.routes.chat.run_context.get_installed_skill",
        lambda skill_id: installed.get(skill_id),
    )
    monkeypatch.setattr(
        "src.server.routes.chat.run_context.get_effective_model_settings",
        lambda: SimpleNamespace(default_model="model", fallback_models=[], provider="deepseek", api_key="x"),
    )
    monkeypatch.setattr(
        "src.server.routes.chat.run_context.build_memory_injection_text",
        lambda context: "",
    )
    monkeypatch.setattr(
        "src.server.routes.chat.run_context.get_registered_agent",
        lambda agent_id: SimpleNamespace(
            name="Assistant",
            system_prompt="prompt",
            skills=["skill-ok"],
            model_config_data=None,
            mcp_tools=[],
            knowledge_tools=[],
            guardrails=SimpleNamespace(filesystem_scope=project["rootPath"]),
        ),
    )
    from src.runtime_context import resolve_runtime_context

    workspace = project_job_run_directory(Path(project["rootPath"]), "job-skill", "firing-skill")
    runtime_context = resolve_runtime_context(
        project_id=project["id"],
        session_id="sched-skill",
        run_id="run-skill",
        agent_id="default",
    )
    with pytest.raises(ValueError, match="skill_unbound"):
        asyncio.run(build_scheduled_run_context(
            _job_ns(id="job-skill", skill_ids=["skill-ok", "skill-missing"]),
            _firing_ns(id="firing-skill", job_id="job-skill"),
            run_data=ChatMessage(
                session_id="sched-skill",
                project_id=project["id"],
                message="使用技能",
                agent_id="default",
                skill_ids=["skill-ok", "skill-missing"],
            ),
            runtime_context=runtime_context,
            session_workspace_dir=str(workspace),
        ))


def test_confirm_scripts_skill_requires_trusted_tools(isolated_db, tmp_path, monkeypatch):
    service = _service()
    skill_dir = tmp_path / "skill-pack"
    (skill_dir / "scripts").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# skill\n", encoding="utf-8")
    job = _weekly_job(service, name="脚本周报", skill_ids=["skill-scripts"])
    monkeypatch.setattr(
        "src.scheduler.unattended_policy.get_installed_skill",
        lambda skill_id: Skill.model_validate({
            "id": "skill-scripts",
            "name": "scripts",
            "description": "",
            "category": "test",
            "status": "installed",
            "enabled": True,
            "resourcePath": str(skill_dir),
            "updatedAt": "2026-08-21T00:00:00+00:00",
        }) if skill_id == "skill-scripts" else None,
    )
    monkeypatch.setattr(
        "src.scheduler.service.get_registered_agent",
        lambda agent_id: SimpleNamespace(
            skills=["skill-scripts"],
            guardrails=SimpleNamespace(
                tool_permission_profile="confirm_destructive",
                tool_permission_mode="accept_edits",
            ),
        ),
    )
    with pytest.raises(ScheduledJobValidationError, match="would_fail_tools: Bash"):
        service.confirm(job.id)

    monkeypatch.setattr(
        "src.scheduler.service.get_registered_agent",
        lambda agent_id: SimpleNamespace(
            skills=["skill-scripts"],
            guardrails=SimpleNamespace(
                tool_permission_profile="trusted_tools",
                tool_permission_mode="bypass",
            ),
        ),
    )
    confirmed = service.confirm(job.id)
    assert confirmed.status == "active"
    assert confirmed.tool_scope.allow_bash is True


def test_confirm_ask_every_time_markdown_job_allows_cwd_write(isolated_db, monkeypatch):
    service = _service()
    job = _weekly_job(service, name="Markdown周报")
    monkeypatch.setattr(
        "src.scheduler.service.get_registered_agent",
        lambda agent_id: SimpleNamespace(
            skills=[],
            guardrails=SimpleNamespace(
                tool_permission_profile="ask_every_time",
                tool_permission_mode="ask_every_time",
            ),
        ),
    )
    confirmed = service.confirm(job.id)
    assert confirmed.status == "active"
    assert would_fail_unattended_tools(
        tool_scope=confirmed.tool_scope,
        agent=SimpleNamespace(guardrails=SimpleNamespace(
            tool_permission_profile="ask_every_time",
        )),
    ) == []


def test_confirm_read_only_cannot_activate_write_job(isolated_db, monkeypatch):
    service = _service()
    job = _weekly_job(service, name="只读周报")
    monkeypatch.setattr(
        "src.scheduler.service.get_registered_agent",
        lambda agent_id: SimpleNamespace(
            skills=[],
            guardrails=SimpleNamespace(
                tool_permission_profile="read_only",
                tool_permission_mode="accept_edits",
            ),
        ),
    )
    with pytest.raises(ScheduledJobValidationError, match="would_fail_tools"):
        service.confirm(job.id)


def test_shared_resume_builder_is_not_team_decision():
    """共享的是事件构造，不是 Team deny-only 决策。"""

    tool_calls = [{
        "id": "call-1",
        "name": "Write",
        "input": '{"file_path":"outputs/a.md"}',
        "state": "asking",
    }]
    approved = build_automatic_confirm_resume(
        event_type="REQUIRE_USER_CONFIRM",
        reply_id="r1",
        tool_calls=tool_calls,
        confirmed_by_id={"call-1": True},
    )
    denied = build_automatic_confirm_resume(
        event_type="REQUIRE_USER_CONFIRM",
        reply_id="r1",
        tool_calls=tool_calls,
        default_confirmed=False,
    )
    assert approved.confirm_results[0].confirmed is True
    assert denied.confirm_results[0].confirmed is False


def _prepare_project(tmp_path) -> dict:
    ensure_db()
    root = tmp_path / "job-project"
    root.mkdir()
    return create_project({
        "id": "proj-unattended",
        "workspace_id": DEFAULT_WORKSPACE_ID,
        "name": "无人值守项目",
        "root_path": str(root),
    })


class _FakeAskEveryTimeWriteAgent:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.resumes: list[object] = []

    async def reply_stream(self, user_msg):
        from agentscope.event import UserConfirmResultEvent

        if isinstance(user_msg, UserConfirmResultEvent):
            self.resumes.append(user_msg)
            path = self.workspace / "outputs" / "weekly-report.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# 周报\n", encoding="utf-8")
            yield _FakeAgentScopeEvent({
                "type": "TEXT_BLOCK_DELTA",
                "delta": "已写入周报",
            })
            yield _FakeAgentScopeEvent({"type": "REPLY_END", "reply_id": "reply-1"})
            return
        yield _FakeAgentScopeEvent({
            "type": "REQUIRE_USER_CONFIRM",
            "reply_id": "reply-1",
            "tool_calls": [{
                "id": "call-write",
                "name": "Write",
                "input": '{"file_path":"outputs/weekly-report.md"}',
                "state": "asking",
            }],
        })


class _FakeAlwaysBashHitlAgent:
    async def reply_stream(self, user_msg):
        yield _FakeAgentScopeEvent({
            "type": "REQUIRE_USER_CONFIRM",
            "reply_id": "reply-bash",
            "tool_calls": [{
                "id": "call-bash",
                "name": "Bash",
                "input": '{"command":"ls"}',
                "state": "asking",
            }],
        })
