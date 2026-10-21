"""Chat 公开组装的真实 SDK/持久审批/文件结果回归，不调用真实模型。"""

from types import SimpleNamespace

import pytest
from agentscope.event import RequireUserConfirmEvent
from agentscope.message import ToolCallBlock, ToolCallState

from src.agents.permission_adapter import PlatformAgent
from src.agents.runtime_types import RuntimeToolScope
from src.runtime.authorization_resources import RuntimeCapabilityReader
from src.runtime_context import resolve_runtime_context
from src.security.authorization.models import RunAuthorizationMetadata
from src.security.authorization.models import ResourceRevision
from src.server.routes.chat.authorization import build_chat_authorization
from src.server.routes.chat.authorization_display import public_authorization_event
from src.server.routes.chat.background_runs import CURRENT_BACKGROUND_RUN_ID
from src.server.routes.chat.hitl import _build_resume_event, _create_pending_interaction, _hitl_payload
from src.server.routes.chat.models import ChatResumeRequest
from src.storage import database
from src.tools.scoped_write import ScopedWrite
from test.authorization_fakes import collect, make_agent, run_async


async def chat_runtime(tmp_path, monkeypatch, mode="manual", plan=None):
    database.ensure_db()
    from src.server.routes import agents
    await agents.update_agent("default", agents.AgentUpdate(guardrails=agents.Guardrails(filesystem_scope=str(tmp_path))))
    project = database.create_project({"name": "权限验证", "primaryPath": str(tmp_path)})
    project_slug = project["slug"]
    context = resolve_runtime_context(project_slug=project_slug, session_id="approval-session", run_id="approval-run", agent_id="default")
    metadata = RunAuthorizationMetadata(tenantId="local", approvalMode=mode)
    database.upsert_chat_run({"id": context.run_id, "projectSlug": project_slug,
                              "sessionId": context.session_id, "agentId": "default",
                              "authorization": metadata.model_dump(mode="json", by_alias=True)})

    async def run_reader(run_id):
        return SimpleNamespace(id=context.run_id, authorization_metadata=metadata, task_text="修改报告",
                               permission_review_binding=None, authorization_bindings=[])

    monkeypatch.setattr("src.server.routes.chat.authorization.get_chat_background_run", run_reader)
    ctx = SimpleNamespace(runtime_context=context, agent_id="default", session_id=context.session_id,
                          runtime_tool_scope=RuntimeToolScope(), tool_name_denylist=set(), extra_filesystem_scopes=[])
    token = CURRENT_BACKGROUND_RUN_ID.set(context.run_id)
    try:
        binding = await build_chat_authorization(ctx, plan_mode_manager=plan)
    finally:
        CURRENT_BACKGROUND_RUN_ID.reset(token)
    tool = ScopedWrite(base_dir=str(tmp_path), scope_dir=str(tmp_path))
    call = ToolCallBlock(id="one-write", name=tool.name, input='{"file_path":"report.md","content":"new report"}')
    agent = make_agent(tool, [call], PlatformAgent, authorization_service=binding.service, action_builder=binding.action_builder)
    agent.bind_authorization(binding)
    return agent, call, binding, ctx


@run_async
async def test_chat_manual_human_approval_is_persistent_exact_and_publicly_redacted(tmp_path, monkeypatch):
    agent, call, binding, ctx = await chat_runtime(tmp_path, monkeypatch)
    events = await collect(agent._execute_tool_call(call))
    require = next(event for event in events if isinstance(event, RequireUserConfirmEvent))
    assert not (tmp_path / "report.md").exists()
    interaction = _create_pending_interaction(interaction_type="REQUIRE_USER_CONFIRM", agent=agent,
         session_id=ctx.session_id, agent_id=ctx.agent_id, agent_name="测试", model_name="fake",
         reply_id=agent.state.reply_id, tool_calls=[item.model_dump(mode="json") for item in require.tool_calls],
         message="修改报告", assistant_text="", skill_runs=[], run_id=ctx.runtime_context.run_id, persist=False)
    payload = _hitl_payload(interaction)
    assert payload["approvalMode"] == "manual" and len(payload["decisions"]) == 1
    assert "new report" in payload["toolCalls"][0]["input"]
    assert payload["decisions"][0]["status"] == "pending"
    assert public_authorization_event(agent, "TOOL_CALL_DELTA", {"delta": "Bearer secret"})["delta"] == ""
    resume = _build_resume_event(interaction, ChatResumeRequest(interactionId=interaction.id, action="approve", rememberApproval=True))
    assert resume.confirm_results[0].rules is None
    with pytest.raises(ValueError):
        _build_resume_event(interaction, ChatResumeRequest(interactionId=interaction.id, action="approve"))
    await collect(agent._handle_incoming_event(resume))
    await collect(agent._execute_tool_call(call))
    assert (tmp_path / "report.md").read_text() == "new report"
    assert agent.authorization_details(call.id)["executionStatus"] == "succeeded"
    assert await collect(agent._execute_tool_call(call)) == []


@pytest.mark.parametrize("changed", ["plan", "resource", "agent_capability", "archived"])
@run_async
async def test_full_access_cannot_override_current_capability_or_plan(tmp_path, monkeypatch, changed):
    plan = SimpleNamespace(is_plan_active=lambda: True) if changed == "plan" else None
    agent, call, binding, ctx = await chat_runtime(tmp_path, monkeypatch, "full_access", plan)
    if changed == "resource":
        reader = binding.service.boundary_check.capabilities
        reader.denied_tool_names = frozenset({call.name})
    elif changed == "agent_capability":
        from src.server.routes import agents
        await agents.update_agent("default", agents.AgentUpdate(guardrails=agents.Guardrails(filesystem_scope=str(tmp_path), network_enabled=False)))
    elif changed == "archived":
        database.update_project(ctx.runtime_context.project_slug, {"status": "archived"})
    await collect(agent._execute_tool_call(call))
    assert not (tmp_path / "report.md").exists()


@run_async
async def test_visible_tool_names_come_from_real_toolkit_not_action_registration(tmp_path, monkeypatch):
    agent, call, binding, ctx = await chat_runtime(tmp_path, monkeypatch, "full_access")
    reader = binding.service.boundary_check.capabilities
    assert call.name in (await reader()).available_tool_names
    alien = ScopedWrite(base_dir=str(tmp_path), scope_dir=str(tmp_path))
    alien.name = "NeverInToolkit"
    action = await binding.action_builder(alien, "alien", '{"file_path":"alien.md","content":"no"}')
    assert (await binding.service.authorize(action)).source == "boundary"
    assert not (tmp_path / "alien.md").exists()


@run_async
async def test_capability_version_ignores_project_metadata_and_skill_body(tmp_path, monkeypatch):
    """普通 Project/Skill 内容变化不能让当前 Run 的无关工具全局自锁。"""

    agent, call, binding, ctx = await chat_runtime(tmp_path, monkeypatch, "full_access")
    reader = binding.service.boundary_check.capabilities
    skill_dir = tmp_path / ".agents" / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo\ndescription: demo skill\n---\noriginal\n", encoding="utf-8",
    )
    before = (await reader()).version
    database.update_project(ctx.runtime_context.project_slug, {"color": "#112233"})
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo\ndescription: demo skill\n---\nupdated\n", encoding="utf-8",
    )
    after = (await reader()).version
    assert after == before


@run_async
async def test_action_resource_revision_is_explicit_and_fingerprinted(tmp_path):
    from src.security.authorization.actions import RuntimeActionBuilder
    from src.tools.scoped_write import ScopedWrite
    from test.test_permission_records import action_for

    owner = action_for(approvalMode="full_access").authorization
    revision = ResourceRevision(resourceType="skill", resourceId="path:demo", revision="sha-a")
    first_builder = RuntimeActionBuilder(owner, filesystem_scope=str(tmp_path), resource_revisions=(revision,))
    tool = ScopedWrite(base_dir=str(tmp_path), scope_dir=str(tmp_path))
    first = await first_builder(tool, "call-1", '{"file_path":"demo.txt","content":"one"}')
    second_builder = RuntimeActionBuilder(
        owner, filesystem_scope=str(tmp_path),
        resource_revisions=(revision.model_copy(update={"revision": "sha-b"}),),
    )
    second = await second_builder(tool, "call-1", '{"file_path":"demo.txt","content":"one"}')
    assert first.resource_revisions[0].revision == "sha-a"
    assert first.fingerprint != second.fingerprint


@run_async
async def test_batch_approval_covers_only_listed_calls(tmp_path, monkeypatch):
    """一次批量批准只覆盖卡片列出的精确调用；同工具的新调用不继承。"""

    agent, first_call, binding, ctx = await chat_runtime(tmp_path, monkeypatch)
    tool_name = first_call.name
    calls = [
        first_call,
        ToolCallBlock(id="write-two", name=tool_name, input='{"file_path":"two.md","content":"two"}'),
        ToolCallBlock(id="write-three", name=tool_name, input='{"file_path":"three.md","content":"three"}'),
    ]
    # 同一模型回复中的三个调用都进入待批准状态。
    pending_tool_calls = []
    for call in calls:
        events = await collect(agent._execute_tool_call(call))
        require = next(event for event in events if isinstance(event, RequireUserConfirmEvent))
        pending_tool_calls.extend(item.model_dump(mode="json") for item in require.tool_calls)
    assert not (tmp_path / "report.md").exists()

    interaction = _create_pending_interaction(
        interaction_type="REQUIRE_USER_CONFIRM", agent=agent,
        session_id=ctx.session_id, agent_id=ctx.agent_id, agent_name="测试", model_name="fake",
        reply_id=agent.state.reply_id, tool_calls=pending_tool_calls,
        message="批量写入", assistant_text="", skill_runs=[], run_id=ctx.runtime_context.run_id, persist=False)
    resume = _build_resume_event(interaction, ChatResumeRequest(interactionId=interaction.id, action="approve"))
    assert len(resume.confirm_results) == 3
    await collect(agent._handle_incoming_event(resume))
    for call in calls:
        await collect(agent._execute_tool_call(call))
    assert (tmp_path / "report.md").read_text() == "new report"
    assert (tmp_path / "two.md").read_text() == "two"
    assert (tmp_path / "three.md").read_text() == "three"

    # 第四个同工具新调用不继承前三个批准。
    fourth = ToolCallBlock(id="write-four", name=tool_name, input='{"file_path":"four.md","content":"four"}')
    events = await collect(agent._execute_tool_call(fourth))
    assert isinstance(events[-1], RequireUserConfirmEvent)
    assert not (tmp_path / "four.md").exists()
