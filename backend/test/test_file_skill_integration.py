"""真实 Toolkit、REST、INDEX、冻结 Job 与激活恢复的同包身份回归。"""
import asyncio
import hashlib
import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest
from agentscope.state import AgentState
from agentscope.message import AssistantMsg, HintBlock, TextBlock, ToolResultBlock, UserMsg

from src.skills import build_skill_catalog, current_skill_context, read_skill, resolve_skill, SkillError
from src.skills.service import list_catalog_skills, get_catalog_skill, catalog_for
from src.skills.registry import resolve_runtime_skill_paths
from src.skills.policy import set_skill_policy
from src.agents.factory import _create_default_toolkit
from src.scheduler.skill_manifest import build_required_skill_manifest, validate_required_skill_manifest, SkillManifestError
from src.server.routes.chat.skill_context_middleware import activate_forced_skills, activated_skills, validate_activated_skills, activate_skill
from src.storage import database


def make_project(tmp_path, project_id="file-project", workspace="default"):
    root = tmp_path / project_id
    root.mkdir()
    return database.create_project({"id": project_id, "workspaceId": workspace, "name": project_id, "rootPath": str(root)})


def write_package(root, relative=".agents/skills/report", name="report", body="执行日报步骤 A"):
    path = Path(root) / relative
    path.mkdir(parents=True, exist_ok=True)
    (path / "SKILL.md").write_text(f"---\nname: {name}\ndescription: 日报任务说明\n---\n\n{body}\n", encoding="utf-8")
    return path


def write_managed_package(relative="report", name="report", body="执行日报步骤 A"):
    return write_package(current_skill_context().tenant_root / "skills", relative, name, body)


@pytest.mark.parametrize("dirty_db", [False, True])
def test_entry_matrix_with_database_source_disabled(tmp_path, monkeypatch, dirty_db):
    project = make_project(tmp_path)
    path = write_managed_package()
    if dirty_db:
        database.replace_skills({"stale-row": {"id": "stale-row", "name": "report", "content": "旧正文不可执行", "description": "旧数据库", "category": "test", "status": "installed", "enabled": True}})
    def deny_db(*args, **kwargs):
        raise AssertionError("文件加载不得读取旧登记源")
    monkeypatch.setattr(database, "list_skills", deny_db)
    monkeypatch.setattr(database, "list_json_records", deny_db)
    from src.server.routes.skills import list_skills, get_skill, export_skill, list_enabled_skills
    listing = asyncio.run(list_skills(project_id=project["id"]))
    item = next(item for item in listing if item.name == "report")
    assert not item.content and item.content_hash == hashlib.sha256((path / "SKILL.md").read_bytes()).hexdigest()
    detail = asyncio.run(get_skill(item.id, project_id=project["id"]))
    enabled = asyncio.run(list_enabled_skills(project_id=project["id"]))
    assert detail.id in {item.id for item in enabled}
    from src.catalog.index import _skill_index_rows
    assert any(detail.load_name in row and detail.resource_path in row for row in _skill_index_rows(Path(project["rootPath"]), "default"))
    catalog = catalog_for()
    viewer_item = get_catalog_skill("report", catalog)
    paths = resolve_runtime_skill_paths([viewer_item])
    toolkit = _create_default_toolkit(skill_paths=paths)
    async def view():
        index = await toolkit.get_skill_instructions()
        assert detail.load_name in index and detail.content not in index
        target = (await toolkit._get_available_skills())[detail.load_name]
        chunk = await toolkit.builtin_skill_viewer.tool.call(skill=detail.load_name, _agent_state=AgentState())
        return target, chunk
    target, chunk = asyncio.run(view())
    assert chunk.content[0].text == detail.content
    ids, frozen, live = build_required_skill_manifest(job_skill_ids=[item.id], project_root=Path(project["rootPath"]))
    assert ids == [detail.id] and frozen[0].load_name == detail.load_name
    assert frozen[0].content_sha256 == detail.content_hash
    response = asyncio.run(export_skill(item.id, project_id=project["id"]))
    async def collect():
        return b"".join([chunk async for chunk in response.body_iterator])
    with zipfile.ZipFile(BytesIO(asyncio.run(collect()))) as archive:
        assert archive.read("report/SKILL.md").decode() == detail.content
    (path / "SKILL.md").write_text("---\ndescription: edited\n---\n新规则")
    with pytest.raises(SkillError) as stale:
        _ = target.markdown
    assert stale.value.code == "skill_catalog_stale"


def test_sdk_duplicate_names_use_exact_packages_and_lifecycle_matches(tmp_path):
    project = make_project(tmp_path)
    write_managed_package("left", body="LEFT 正文")
    write_managed_package("right", body="RIGHT 正文")
    catalog = catalog_for()
    skills = [item for item in list_catalog_skills(catalog) if item.enabled]
    toolkit = _create_default_toolkit(skill_paths=resolve_runtime_skill_paths(skills))
    async def view_all():
        available = await toolkit._get_available_skills()
        assert len(available) == 2
        for item in skills:
            result = await toolkit.builtin_skill_viewer.tool.call(skill=item.load_name, _agent_state=AgentState())
            assert result.content[0].text == read_skill(resolve_skill(item.id, catalog)).markdown
    asyncio.run(view_all())
    from src.server.routes.chat.leader_skill_lifecycle import skill_lifecycle_events
    from src.server.routes.chat.models import ChatToolRun
    from src.server.routes.chat.leader_stream import LeaderStreamState
    from src.skills.registry import list_skill_usage_details
    state = LeaderStreamState()
    state.available_skill_details = list_skill_usage_details(skills)
    state.assistant_tool_calls = [ChatToolRun(id="call", name="Skill", args=json.dumps({"skill": skills[1].load_name}))]
    runs = []
    skill_lifecycle_events(event_type="TOOL_RESULT_END", event_data={"tool_call_id": "call", "tool_call_name": "Skill", "state": "success"}, state=state, skill_runs=runs, session_id="", persist=False, agent_id="default", agent_name="Test")
    assert runs[0].id == skills[1].id and runs[0].content_hash == skills[1].content_hash


def test_activation_revalidation_replaces_only_controlled_blocks(tmp_path):
    project = make_project(tmp_path)
    path = write_managed_package()
    catalog = catalog_for()
    skill = get_catalog_skill("report", catalog)
    agent = SimpleNamespace(name="test", state=AgentState(), _aw_resolved_skills={skill.id: skill._resolved_skill})
    user = UserMsg(name="human", content="执行日报步骤 A 是我的原始文字")
    agent.state.context.append(user)
    activate_forced_skills(agent, [skill])
    assert skill.id in activated_skills(agent)
    old_body = skill.content
    (path / "SKILL.md").write_text("---\nname: report\ndescription: 新版本\n---\n新规则 B")
    new_catalog = catalog_for()
    new = resolve_skill(skill.id, new_catalog)
    agent._aw_resolved_skills = {skill.id: new}
    assert validate_activated_skills(agent)[0]["reason"] == "skill_version_changed"
    assert activated_skills(agent)[skill.id]["contentHash"] == new.candidate.content_hash
    hints = " ".join(str(getattr(block, "hint", "")) for msg in agent.state.context for block in msg.content)
    assert old_body not in hints and "已失效" in hints
    assert user.content[0].text == "执行日报步骤 A 是我的原始文字"
    agent._aw_resolved_skills = {}
    assert validate_activated_skills(agent)[0]["reason"] == "skill_unbound"
    assert not activated_skills(agent)


def test_job_freeze_remains_concrete_when_name_priority_changes(tmp_path):
    project = make_project(tmp_path)
    context = current_skill_context()
    workspace = write_package(context.tenant_root / "skills", "report", body="冻结统一目录版本")
    catalog = build_skill_catalog(context)
    original = resolve_skill("report", catalog).candidate
    ids, manifest, _ = build_required_skill_manifest(job_skill_ids=[original.id], project_root=context.tenant_root)
    write_package(tmp_path, body="不应参与加载的项目版本")
    _, live, _ = validate_required_skill_manifest(manifest, canonical_ids=ids, project_root=context.tenant_root)
    assert "冻结统一目录版本" in live[original.id].markdown
    assert live[original.id].source_path == str(workspace / "SKILL.md")


def test_management_context_isolation_and_no_sql_missing_bypass(tmp_path):
    from src.project_resources import resolve_project_resource_access
    from src.runtime_context import RuntimeContext
    project = make_project(tmp_path, "one")
    other = make_project(tmp_path, "two")
    write_managed_package()
    catalog = catalog_for()
    item = resolve_skill("report", catalog).candidate
    assert get_catalog_skill(item.id, catalog_for()).id == item.id
    context = RuntimeContext(workspace_id="default", project_id=other["id"], project_root=Path(other["rootPath"]), session_id="", run_id="", agent_id="default")
    access = resolve_project_resource_access(context)
    assert access.filter_ids("skill", [item.id, "arbitrary-missing-row"]) == []


def test_delete_managed_skill_cleans_policy_without_job_scan(tmp_path, monkeypatch):
    from src.server.routes.skills import delete_skill
    from src.skills.policy import read_policy, update_policy
    project = make_project(tmp_path)
    path = write_managed_package()
    catalog = catalog_for()
    item = resolve_skill("report", catalog).candidate
    update_policy(catalog.context.policy_path, lambda policy: policy.legacy_aliases.update({"old-report": [item.id]}))
    monkeypatch.setattr(
        "src.scheduler.service.ScheduledJobService.list_jobs",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("删除 Skill 不得扫描 Job")),
    )
    asyncio.run(delete_skill(item.id, project_id=project["id"]))
    assert not path.exists()
    policy = read_policy(catalog.context.policy_path)
    assert item.id not in policy.overrides
    assert "old-report" not in policy.legacy_aliases


def test_delete_invalid_managed_skill_without_job_scan(tmp_path, monkeypatch):
    """损坏的受管包仍可从管理页删除，且删除不进入 Job 领域。"""

    from src.server.routes.skills import delete_skill

    project = make_project(tmp_path)
    path = current_skill_context().tenant_root / "skills" / "broken"
    path.mkdir(parents=True)
    (path / "SKILL.md").write_text("---\nname: [broken\n---\n无法解析\n", encoding="utf-8")
    catalog = catalog_for()
    item = next(item for item in list_catalog_skills(catalog) if item.resource_path == str(path))
    assert item.status == "error"
    assert item.managed is True
    monkeypatch.setattr(
        "src.scheduler.service.ScheduledJobService.list_jobs",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("删除 Skill 不得扫描 Job")),
    )

    asyncio.run(delete_skill(item.id, project_id=project["id"]))

    assert not path.exists()


def test_daily_0900_feasibility_firing_fresh_version_failure_reconfirm(tmp_path, monkeypatch):
    """每天 09:00 的文件 Skill 真链路；spy 只替换最后模型执行，失败不能到达 spy。"""
    from src.scheduler.service import ScheduledJobService
    from src.scheduler.settings import set_scheduled_jobs_settings
    from src.scheduler.capability_catalog import build_agent_capability_inventory
    from src.scheduler.firing import run_firing
    from src.server.routes.chat.run_context import build_scheduled_run_context
    from src.server.routes.chat.models import ChatMessage
    from src.runtime_context import resolve_runtime_context
    from src.skills.registry import register_user_skill
    from src.project_files import project_job_run_directory
    # 使用当前已有正式 Project 和真实 Job profile，feasibility 库存来自文件。
    project = make_project(tmp_path, "daily-project")
    path = write_managed_package()
    managed = resolve_skill("report", catalog_for()).candidate
    database.upsert_project_resource_binding({
        "project_id": project["id"],
        "resource_type": "skill",
        "resource_id": managed.id,
        "enabled": True,
    })
    database.upsert_agent({"id": "daily-agent", "name": "日报执行者", "role": "job", "workspaceId": "default",
        "skills": [managed.id], "system_prompt": "执行定时日报", "guardrails": {"filesystem_scope": project["rootPath"]}, "jobToolkit": {"required": [{"kind": "builtin", "id": "Bash"}], "optional": [{"kind": "skill", "id": managed.id}]}})
    set_scheduled_jobs_settings(enabled=True, capability_gate=True)
    inventory = build_agent_capability_inventory("daily-agent", project["id"], "default")
    skill_cap = next(item for item in inventory if item.kind == "skill")
    assert skill_cap.project_bound and skill_cap.agent_bound
    assert skill_cap.unattended_ok
    from src.scheduler.feasibility import evaluate_job_feasibility
    feasibility = evaluate_job_feasibility(task_intent="生成日报", agent_id="daily-agent", workspace_id="default",
        project_id=project["id"], claimed_must_include=frozenset({skill_cap.capability_id}))
    assert feasibility.verdict != "impossible"
    service = ScheduledJobService()
    job = service.create_job(workspace_id="default", project_id=project["id"], name="每天九点日报", agent_id="daily-agent",
        frozen_prompt="生成日报", task_intent="生成日报", skill_ids=[skill_cap.resource_id],
        capability_contract={"verdict": "generate_only"}, schedule_kind="daily", schedule_expr={"at": "09:00"}, timezone="Asia/Shanghai")
    job = service.confirm(job.id)
    frozen_hash = job.required_skills[0].content_sha256
    calls, contexts, sessions = [], [], []
    async def model_spy(**kwargs):
        # bridge 已在此之前完成 manifest 校验；用真实 Context 装配证明首轮预加载。
        session = kwargs["session_id"]
        from src.storage import database as job_database

        with job_database._lock, job_database._connect() as conn:
            conn.execute(
                "UPDATE chat_sessions SET working_directory = ? WHERE id = ?",
                (project["rootPath"], session),
            )
            conn.commit()
        data = ChatMessage(session_id=session, project_id=project["id"], agent_id=job.agent_id, message=job.frozen_prompt)
        from src.sessions.run_access import RunResourceAccess
        import src.server.routes.chat.run_context as run_context_module

        run_context_module.run_resource_access = lambda run_id: RunResourceAccess(
            session_id=session,
            agent_id=job.agent_id,
            skill_ids=frozenset({skill_cap.resource_id}),
            mcp_server_ids=frozenset(),
            http_tool_ids=frozenset(),
        )
        runtime = resolve_runtime_context(project_id=project["id"], session_id=session, run_id="spy-run", agent_id=job.agent_id)
        firing = service.get_firing(kwargs["firing_id"])
        current_job = service.get_job(job.id)
        ctx = await build_scheduled_run_context(current_job, firing, run_data=data, runtime_context=runtime,
            session_workspace_dir=str(project_job_run_directory(runtime.project_root, job.id, firing.id)))
        agent = SimpleNamespace(name="Spy", state=AgentState(), _aw_resolved_skills={skill.id: skill._resolved_skill for skill in ctx.used_skills})
        activate_forced_skills(agent, ctx.forced_skills)
        corpus = " ".join(str(getattr(block, "hint", "")) for msg in agent.state.context for block in msg.content)
        assert "<activated-skill" in corpus and "执行日报步骤 A" in corpus
        assert "执行日报步骤 A" not in ctx.system_prompt
        assert not ctx.session_history
        calls.append(corpus)
        contexts.append(agent.state)
        sessions.append(session)
        return "spy-" + session
    # bridge 的公开 launch 参数名称通过正式函数合同传递。
    monkeypatch.setattr("src.scheduler.chat_bridge.launch_scheduled_chat_run", model_spy)
    async def session_id(run):
        return sessions[-1]
    async def completed(*args, **kwargs):
        return "completed"
    monkeypatch.setattr("src.scheduler.firing._chat_session_id", session_id)
    monkeypatch.setattr("src.scheduler.firing.await_scheduled_chat_run", completed)
    async def fire():
        firing = service.run_now(job.id)
        await run_firing(job.id, firing.id, service=service, semaphore=asyncio.Semaphore(1))
        return service.get_firing(firing.id)
    first = asyncio.run(fire())
    assert first.status == "succeeded", first.error_message
    second = asyncio.run(fire())
    assert second.status == "succeeded" and len(set(sessions)) == 1
    assert contexts[0] is not contexts[1]
    assert first.output_manifest["skillContext"]["items"][0]["contentSha256"] == frozen_hash
    with (path / "SKILL.md").open("a") as stream:
        stream.write("\n新增约束")
    failed = asyncio.run(fire())
    assert failed.status == "failed" and failed.error_code == "skill_version_changed"
    assert len(calls) == 2  # 校验失败没有创建模型执行。
    assert failed.output_manifest["skillContext"]["validation"] == "failed"
    notes = database.list_user_notifications()
    assert any(item.get("firing_id") == failed.id for item in notes)
    service.pause(job.id)
    with pytest.raises(SkillManifestError) as error:
        service.resume(job.id)
    assert error.value.code == "skill_version_changed"
    updated = service.update_job(job.id, {"frozen_prompt": "生成日报（更新规则）"})
    assert updated.status == "pending_confirmation"
    confirmed = service.confirm(job.id)
    assert confirmed.required_skills[0].content_sha256 != frozen_hash


def test_docker_projection_uses_id_for_same_basename(tmp_path):
    from src.skills.docker import FileSkillProjectionMixin
    project = make_project(tmp_path)
    write_managed_package("left", body="LEFT")
    write_managed_package("right", body="RIGHT")
    skills = [item for item in list_catalog_skills(catalog_for()) if item.enabled]
    paths = resolve_runtime_skill_paths(skills)
    commands, archives = [], []
    class Backend:
        async def write_file(self, path, data):
            archives.append(data)
        async def exec_shell(self, command):
            commands.append(command)
            return SimpleNamespace(ok=lambda: True)
    class Workspace(FileSkillProjectionMixin):
        _backend = Backend()
        _skills_dir = "/workspace/skills"
        _aw_skill_paths = paths
    asyncio.run(Workspace()._copy_local_skills())
    assert len(commands) == 2 and len({command[-1] for command in commands}) == 2
    assert {command[-1] for command in commands} == {item.id for item in skills}
    assert all(command[-2] == "/workspace/skills" for command in commands)
    from src.skills.sdk import FileSkillLoader, runtime_package_name
    for path in paths:
        loader = FileSkillLoader(path, "/workspace/skills/" + runtime_package_name(path))
        item = asyncio.run(loader.list_skills())[0]
        assert item.dir.endswith(path.resolved_skill.candidate.id)


def test_subagent_explicit_skill_uses_same_source_without_shared_inheritance(tmp_path, monkeypatch):
    from src.subagents.runtime_context import build_subagent_runtime_context
    from src.subagents.models import SubagentDefinition
    from src.runtime_context import RuntimeContext
    project = make_project(tmp_path)
    write_managed_package("report", name="report")
    write_managed_package("other", name="other")
    expected = resolve_skill("report", catalog_for()).candidate
    database.upsert_project_resource_binding({
        "project_id": project["id"],
        "resource_type": "skill",
        "resource_id": expected.id,
        "enabled": True,
    })
    runtime = RuntimeContext(workspace_id="default", project_id=project["id"], project_root=Path(project["rootPath"]), session_id="child", run_id="child-run", agent_id="default")
    monkeypatch.setattr("src.subagents.runtime_context.build_actor_memory_prompt", lambda **kwargs: "")
    definition = SubagentDefinition(name="child", description="test", skills=[expected.id], prompt="execute", scope="project", path="/tmp/test-child.md")
    context = build_subagent_runtime_context(
        definition,
        runtime,
        parent_snapshot={"resources": {"items": [{"resourceType": "skill", "resourceId": expected.id}]}},
    )
    assert [item.id for item in context.loaded_skills] == [expected.id]
    assert str(context.skill_paths[0]) == str(expected.package_path)
    assert not context.missing_skill_refs
