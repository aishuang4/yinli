"""真实 SDK、平台存储与受控文件工具的一次授权闭环。"""

import asyncio
from dataclasses import replace

import pytest
from agentscope.message import ToolCallBlock, ToolCallState

from src.agents.permission_adapter import PlatformAgent
from src.runtime.authorization import RunAuthorizationBinding
from src.security.authorization.actions import RuntimeActionBuilder
from src.security.authorization.boundaries import RuntimeAuthorizationBoundary, RuntimeCapabilities
from src.security.authorization.service import PlatformAuthorizationService
from src.tools.scoped_write import ScopedWrite
from test.authorization_fakes import collect, make_agent, run_async
from test.test_permission_records import action_for


async def domain_valid(action, tool, raw):
    return None


def runtime(tmp_path, mode="manual"):
    owner = action_for(approvalMode=mode).authorization
    builder = RuntimeActionBuilder(owner)
    tool = ScopedWrite(base_dir=str(tmp_path), scope_dir=str(tmp_path))
    current = [RuntimeCapabilities(version="1", filesystem_scope=str(tmp_path), available_tool_names=frozenset({tool.name}))]
    async def capabilities():
        return current[0]
    boundary = RuntimeAuthorizationBoundary(builder=builder, capabilities=capabilities, domain_check=domain_valid)
    service = PlatformAuthorizationService(boundary_check=boundary, revalidate=builder.revalidate)
    call = ToolCallBlock(id="write-call", name=tool.name, input='{"file_path":"new.md","content":"approved content"}')
    agent = make_agent(tool, [call], PlatformAgent, authorization_service=service, action_builder=builder)
    return agent, service, builder, tool, call, current


@run_async
async def test_real_write_requires_exact_approval_and_execution_facts(tmp_path):
    agent, service, builder, tool, call, _ = runtime(tmp_path)
    await collect(agent._execute_tool_call(call))
    assert not (tmp_path / "new.md").exists()
    action = await builder(tool, call.id, call.input)
    decision = service.store.find(action)
    assert decision.verdict == "needs_human"
    assert service.store.answer(decision.id, action.authorization, approved=True, approved_by="local-user")
    call.state = ToolCallState.ALLOWED
    await collect(agent._execute_tool_call(call))
    assert (tmp_path / "new.md").read_text() == "approved content"
    assert service.store.get_execution(decision.id, action.authorization).status == "succeeded"
    assert await collect(agent._execute_tool_call(call)) == []


@pytest.mark.parametrize("restriction", ["deny", "resource_binding", "read_only", "scope", "version", "cancel"])
@run_async
async def test_full_access_real_boundary_blocks_actual_file_write(tmp_path, restriction):
    agent, service, builder, tool, call, current = runtime(tmp_path, "full_access")
    if restriction == "deny":
        current[0] = replace(current[0], denied_tool_names=frozenset({tool.name}))
    if restriction == "resource_binding":
        current[0] = replace(current[0], available_tool_names=frozenset())
    if restriction == "read_only":
        current[0] = replace(current[0], operation_scope="read_only")
    if restriction == "scope":
        other = tmp_path / "narrow"
        other.mkdir()
        current[0] = replace(current[0], filesystem_scope=str(other))
    if restriction == "version":
        current[0] = replace(current[0], version="tightened")
    if restriction == "cancel":
        service.store.invalidate_run(builder.owner)
    await collect(agent._execute_tool_call(call))
    assert not (tmp_path / "new.md").exists()


@run_async
async def test_target_changed_after_approval_is_a_new_action(tmp_path):
    agent, service, builder, tool, call, _ = runtime(tmp_path)
    await collect(agent._execute_tool_call(call))
    first = await builder(tool, call.id, call.input)
    decision = service.store.find(first)
    service.store.answer(decision.id, first.authorization, approved=True, approved_by="local-user")
    (tmp_path / "new.md").write_text("changed independently")
    call.state = ToolCallState.ALLOWED
    await collect(agent._execute_tool_call(call))
    assert (tmp_path / "new.md").read_text() == "changed independently"
    next_action = await builder(tool, call.id, call.input)
    assert service.store.find(next_action).id != decision.id
    assert service.store.get_execution(decision.id, first.authorization) is None


@run_async
async def test_factory_installs_adapter_isolates_mode_and_refreshes_next_run(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from src.agents import factory
    from test.authorization_fakes import MemoryModel, MemoryTool
    from test.test_permission_adapter import MemoryService, build_action
    primary = SimpleNamespace(default_model="fake")
    model_binding = SimpleNamespace(primary=primary, chain=[primary])
    monkeypatch.setattr(factory, "binding_for_model", lambda _: model_binding)
    monkeypatch.setattr(factory, "create_model_chain", lambda *args, **kwargs: MemoryModel())
    factory.clear_all_agents()
    try:
        owner = action_for().authorization
        binding = RunAuthorizationBinding(owner=owner, service=MemoryService(), action_builder=build_action)
        kwargs = dict(agent_id="same-agent", name="contract", model_name="fake", include_default_tools=False,
                      workspace_dir=str(tmp_path), extra_tools=[MemoryTool([])])
        first = factory.get_or_create_agent(**kwargs, authorization=binding)
        assert isinstance(first, PlatformAgent)
        same = factory.get_or_create_agent(**kwargs, authorization=binding)
        assert same is first and same.authorization_service is binding.service
        second_binding = replace(binding, owner=owner.model_copy(update={"run_id": "next"}), service=MemoryService())
        second = factory.get_or_create_agent(**kwargs, authorization=second_binding)
        assert first is second and second.authorization_service is second_binding.service
        open_binding = replace(binding, owner=owner.model_copy(update={"approval_mode": "full_access"}))
        opened = factory.get_or_create_agent(**kwargs, authorization=open_binding)
        assert opened is not first
        other_session = replace(binding, owner=owner.model_copy(update={"session_id": "another"}))
        assert factory.get_or_create_agent(**kwargs, authorization=other_session) is not first
    finally:
        factory.clear_all_agents()


@run_async
async def test_readonly_shell_binary_is_execution_identity_not_project_target(tmp_path):
    from src.agents.guarded_bash import GuardedBash
    owner = action_for().authorization
    builder = RuntimeActionBuilder(owner, filesystem_scope=str(tmp_path))
    tool = GuardedBash([], timeout_seconds=3, cwd=str(tmp_path))
    (tmp_path / "input.txt").write_text("safe input")
    current = RuntimeCapabilities(version="1", filesystem_scope=str(tmp_path),
                                  available_tool_names=frozenset({tool.name}))
    async def capabilities():
        return current
    boundary = RuntimeAuthorizationBoundary(builder=builder, capabilities=capabilities, domain_check=domain_valid)
    action = await builder(tool, "shell", '{"command":"cat input.txt"}')
    assert all(target.target.startswith(str(tmp_path)) for target in action.targets)
    assert await boundary(action) is None
    next_dir = tmp_path / "another"
    next_dir.mkdir()
    tool._cwd = str(next_dir)
    assert await boundary(action) is not None


@run_async
async def test_control_plane_file_action_carries_resource_revision_and_diff(tmp_path):
    from src.security.authorization.actions import RuntimeActionBuilder
    from src.tools.scoped_write import ScopedWrite

    skill_file = tmp_path / ".agents" / "skills" / "demo" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text("old instruction\n", encoding="utf-8")
    owner = action_for(approvalMode="full_access").authorization
    builder = RuntimeActionBuilder(owner, filesystem_scope=str(tmp_path))
    tool = ScopedWrite(base_dir=str(tmp_path), scope_dir=str(tmp_path))
    action = await builder(tool, "control-plane", '{"file_path":".agents/skills/demo/SKILL.md","content":"new instruction\\n"}')
    assert action.effect == "control_plane_mutation"
    assert action.resource_revisions[-1].resource_id == str(skill_file.resolve())
    assert action.resource_revisions[-1].revision
    assert "-old instruction" in action.control_plane_diff
    assert "+new instruction" in action.control_plane_diff
