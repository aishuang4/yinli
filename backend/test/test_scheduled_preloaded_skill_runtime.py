"""scheduled_preloaded 工具集、缓存、首轮完整性与审计契约测试。"""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from agentscope.message import TextBlock, ToolCallBlock, UserMsg
from agentscope.model import ChatModelBase, ChatResponse

from src.agents import factory
from src.agents.runtime_types import (
    SKILL_LOADING_INTERACTIVE,
    SKILL_LOADING_SCHEDULED,
    AgentGuardrails,
    RuntimeToolScope,
)
from src.agents.skill_toolkit import SkillViewerUnavailableError
from src.runtime.agent_spec import build_runtime_agent_cache_key
from src.server.routes.chat.scheduled_skill_preload import (
    SCHEDULED_FIRST_TURN_STEPS,
    mark_scheduled_job_prompt,
    pop_scheduled_skill_audit,
    prepare_scheduled_first_turn,
    publish_scheduled_skill_audit,
    verify_required_skill_context,
)
from src.server.routes.chat.skill_context_middleware import (
    SkillContextMiddleware,
    ensure_activated_skills_in_context,
)
from test.test_scheduled_job_skill_context import project_job as project_job  # noqa: PLC0414 - 复用 pytest fixture


class RecordingSkillModel(ChatModelBase):
    """记录真实 AgentScope 模型入口参数，不访问网络。"""

    def __init__(self):
        super().__init__(credential=None, model="skill-memory", parameters=self.Parameters(), max_retries=0)
        self.requests = []

    async def _call_api(self, model, **kwargs):
        self.requests.append(copy.deepcopy(kwargs))
        return ChatResponse(content=[TextBlock(text="日报已生成。")], is_last=True)


@pytest.fixture()
def fake_skill_models(monkeypatch):
    """保留真实工厂、Toolkit、middleware、AgentScope，仅替换模型网络边界。"""

    primary = SimpleNamespace(default_model="skill-memory")
    binding = SimpleNamespace(primary=primary, chain=[primary])
    models = []

    def create(*args, **kwargs):
        model = RecordingSkillModel()
        models.append(model)
        return model

    monkeypatch.setattr(factory, "binding_for_model", lambda _: binding)
    monkeypatch.setattr(factory, "create_model_chain", create)
    factory.clear_all_agents()
    yield models
    factory.clear_all_agents()


def skill_agent(skill, directory, *, mode=SKILL_LOADING_SCHEDULED, agent_id="skill-agent"):
    """通过唯一工厂入口构造真实 Agent，避免绕开模式和原生工具执行检查。"""

    from src.skills.sdk import SkillRuntimePath

    return factory.get_or_create_agent(
        agent_id=agent_id, name="日报执行者", model_name="skill-memory",
        system_prompt="执行日报，不增加权限。",
        workspace_dir=str(directory), skill_paths=[SkillRuntimePath(skill._resolved_skill)],
        guardrails=AgentGuardrails(filesystem_scope=str(directory)),
        include_default_tools=False, middlewares=[SkillContextMiddleware()],
        skill_loading_mode=mode,
    )


async def scheduled_context(service, job, project, *, suffix="one"):
    """走真实 manifest preflight 和 ChatRunContext 装配。"""

    from src.runtime_context import resolve_runtime_context
    from src.server.routes.chat.models import ChatMessage
    from src.server.routes.chat.run_context import build_scheduled_run_context
    from src.sessions.records import create_session
    from src.sessions.run_binding import begin_run

    firing = service.run_now(job.id)
    session_id = f"skill-session-{suffix}"
    create_session(
        session_id=session_id,
        agent_id=job.agent_id,
        working_directory=project["rootPath"],
    )
    skill_ids = [str(item) for item in (getattr(job, "skill_ids", None) or []) if str(item)]
    begin_run(
        session_id,
        f"skill-run-{suffix}",
        agent_id=job.agent_id,
        start_model=lambda snapshot: snapshot,
    )
    data = ChatMessage(
        session_id=f"skill-session-{suffix}", project_id=project["id"],
        agent_id=job.agent_id, message=job.frozen_prompt,
    )
    runtime = resolve_runtime_context(
        project_id=project["id"], session_id=data.session_id,
        run_id=f"skill-run-{suffix}", agent_id=job.agent_id,
    )
    directory = Path(project["rootPath"]) / "firings" / firing.id
    directory.mkdir(parents=True, exist_ok=True)
    from src.sessions.run_access import RunResourceAccess

    def _allow_frozen_skills(run_id: str) -> RunResourceAccess:
        del run_id
        return RunResourceAccess(
            session_id=session_id,
            agent_id=str(job.agent_id),
            skill_ids=frozenset(skill_ids),
            mcp_server_ids=frozenset(),
            http_tool_ids=frozenset(),
        )

    original = build_scheduled_run_context.__globals__["run_resource_access"]
    build_scheduled_run_context.__globals__["run_resource_access"] = _allow_frozen_skills
    try:
        ctx = await build_scheduled_run_context(
            job, firing, run_data=data, runtime_context=runtime,
            session_workspace_dir=str(directory),
        )
    finally:
        build_scheduled_run_context.__globals__["run_resource_access"] = original
    ctx.persist_run = False
    return ctx, data, firing, directory


def request_text(request, *, role=None):
    """规范化模型收到的消息，便于检查 system 与 conversation 的边界。"""

    result = []
    for message in request.get("messages") or []:
        value = message.model_dump() if hasattr(message, "model_dump") else message
        if role is None or value.get("role") == role:
            result.append(value)
    return json.dumps(result, ensure_ascii=False, default=str)


def test_modes_isolate_same_agent_cache_and_tool_schema(project_job, fake_skill_models):
    """同一 Agent 的两种触发模式不得复用错误的实例或工具 schema。"""

    _, _, skill, project = project_job

    async def run():
        directory = Path(project["rootPath"])
        interactive = skill_agent(skill, directory, mode=SKILL_LOADING_INTERACTIVE)
        scheduled = skill_agent(skill, directory)
        assert interactive is not scheduled
        assert skill_agent(skill, directory, mode=SKILL_LOADING_INTERACTIVE) is interactive
        assert skill_agent(skill, directory) is scheduled
        interactive_names = {
            item["function"]["name"] for item in await interactive.toolkit.get_tool_schemas([])
        }
        scheduled_names = {
            item["function"]["name"] for item in await scheduled.toolkit.get_tool_schemas([])
        }
        assert "Skill" in interactive_names
        assert not ({"Skill", "SkillViewer"} & scheduled_names)
        assert scheduled.toolkit.builtin_skill_viewer is None
        assert [item.id for item in await scheduled.toolkit.list_preloaded_skills()] == [skill.id]

    asyncio.run(run())
    common = {
        "agent_id": "same", "session_id": "same", "system_prompt": "same", "model_name": "same",
        "deep_thinking": False, "fallback_names": [], "used_skills": [], "mcp_servers": [],
        "knowledge_tools": [], "runtime_tool_scope": RuntimeToolScope(), "guardrail_cache_key": "same",
    }
    assert build_runtime_agent_cache_key(**common) != build_runtime_agent_cache_key(
        **common, skill_loading_mode=SKILL_LOADING_SCHEDULED,
    )


def test_catalog_and_prompt_templates_are_mode_specific(project_job, fake_skill_models):
    """交互 catalog 给出稳定 ID；scheduled 说明不引导模型再次加载正文。"""

    from src.server.routes.chat.prompts import _build_scheduled_skill_context_prompt

    _, _, skill, project = project_job

    async def run():
        directory = Path(project["rootPath"])
        interactive = skill_agent(skill, directory, mode=SKILL_LOADING_INTERACTIVE)
        scheduled = skill_agent(skill, directory)
        normal = await interactive.toolkit.get_skill_instructions()
        preloaded = await scheduled.toolkit.get_skill_instructions()
        assert f"<id>{skill.id}</id>" in normal
        assert "<display-name>daily-report</display-name>" in normal
        assert "<description>" in normal and "<dir>" in normal
        assert "ScheduleJob.skill_ids" in normal
        assert 'mode="scheduled_preloaded"' in preloaded
        assert f"<id>{skill.id}</id>" in preloaded
        assert "<name>daily-report</name>" in preloaded and "<dir>" in preloaded
        for text in [preloaded, _build_scheduled_skill_context_prompt()]:
            assert "call the Skill tool" not in text
            assert "call the `Skill`" not in text
            assert "preloaded" in text
            assert "# 日报执行规则" not in text

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["activation", "hash", "body", "duplicate", "schema", "history"])
def test_first_turn_failures_never_call_model(project_job, fake_skill_models, monkeypatch, failure):
    """真实 React runtime 任一首轮注入/断言失败都不得触达模型。"""

    from src.server.routes.chat import scheduled_skill_preload as preload
    from src.server.routes.chat.react_runtime import execute_react_runtime

    service, job, _, project = project_job
    job = service.confirm(job.id)
    original = preload.activate_forced_skills

    def activate(agent, skills):
        if failure == "activation":
            return []
        result = original(agent, skills)
        if failure in {"hash", "body", "duplicate"}:
            message = agent.state.context[-1]
            if failure == "duplicate":
                agent.state.context.append(copy.deepcopy(message))
            else:
                block = message.content[0]
                text = block.hint if isinstance(block.hint, str) else block.hint[0].text
                changed = text.replace(
                    skills[0].content_hash if failure == "hash" else "# 日报执行规则",
                    "wrong-hash" if failure == "hash" else "# 被篡改的正文",
                )
                if isinstance(block.hint, str):
                    block.hint = changed
                else:
                    block.hint[0].text = changed
        return result

    monkeypatch.setattr(preload, "activate_forced_skills", activate)
    if failure == "schema":
        async def invalid_schema(self, activated_groups=None):
            return [{"type": "function", "function": {"name": "Skill"}}]

        monkeypatch.setattr("src.agents.skill_toolkit.PreloadedSkillToolkit.get_tool_schemas", invalid_schema)

    async def run():
        ctx, data, firing, _ = await scheduled_context(service, job, project)
        if failure == "history":
            ctx.session_history = [{"role": "user", "content": "旧会话消息"}]
        events = [event async for event in execute_react_runtime(ctx=ctx, run_data=data)]
        audit = pop_scheduled_skill_audit(firing.id)
        assert events
        assert audit["validation"] == "failed"
        assert audit["errorCode"].startswith("skill_")
        assert audit["failedStep"] in SCHEDULED_FIRST_TURN_STEPS
        assert all(not model.requests for model in fake_skill_models)

    asyncio.run(run())


def test_preload_order_and_reinjection_audit(project_job, fake_skill_models):
    """首轮固定顺序、无正文审计，以及压缩后受控补回保持同一冻结正文。"""

    service, job, skill, project = project_job
    job = service.confirm(job.id)

    async def run():
        ctx, _, firing, directory = await scheduled_context(service, job, project)
        agent = skill_agent(skill, directory)
        audit = await prepare_scheduled_first_turn(agent, ctx, run_snapshot=None)
        mark_scheduled_job_prompt(agent)
        assert audit["firstTurnSteps"] == list(SCHEDULED_FIRST_TURN_STEPS)
        assert audit["injectionOrder"] == [skill.id]
        assert audit["budget"]["estimatedTokens"] > 0
        assert audit["budget"]["totalChars"] <= audit["budget"]["totalMaxChars"]
        assert audit["contextVerification"]["status"] == "passed"
        assert audit["reinjections"] == []
        agent.state.context.clear()
        assert ensure_activated_skills_in_context(agent, reason="compressed")
        assert verify_required_skill_context(agent, ctx.forced_skills)["status"] == "passed"
        assert audit["reinjections"][-1]["reason"] == "compressed"
        assert audit["reinjections"][-1]["skillIds"] == [skill.id]
        from src.server.routes.chat.trace_recorder import build_model_call_context_stats

        trace = build_model_call_context_stats(agent)
        assert trace["skillContext"]["injectionOrder"] == audit["injectionOrder"]
        assert trace["skillContext"]["budget"] == audit["budget"]
        assert trace["skillContext"]["contextVerification"] == audit["contextVerification"]
        assert "通过 Skill 工具读取" not in request_text({"messages": agent.state.context})
        publish_scheduled_skill_audit(firing.id, agent)
        saved = pop_scheduled_skill_audit(firing.id)
        assert "# 日报执行规则" not in json.dumps(saved, ensure_ascii=False)
        assert saved["injectionOrder"] == audit["injectionOrder"]
        assert pop_scheduled_skill_audit(firing.id) is None

    asyncio.run(run())


def test_skill_mount_does_not_expand_tools_or_file_scope(project_job):
    """Skill 包挂载保留普通文件访问边界，不增加 Bash/网络/MCP/审批能力。"""

    from src.server.routes.chat.scheduled_runtime import (
        SCHEDULED_JOB_UNATTENDED_DENYLIST,
    )
    from src.skills.sdk import SkillRuntimePath

    _, _, skill, project = project_job
    directory = Path(project["rootPath"])
    guardrails = AgentGuardrails(filesystem_scope=str(directory), network_enabled=False)
    permission_profile = copy.deepcopy(guardrails.permission_profile)
    scope = RuntimeToolScope(
        allow_mcp=False, allow_knowledge=False, allow_bash=False, allow_http=False, allow_web=False,
    )

    async def run():
        toolkits = [
            factory._create_default_toolkit(
                tool_scope=scope, guardrails=guardrails, workspace_dir=str(directory),
                skill_paths=[SkillRuntimePath(skill._resolved_skill)],
                tool_name_denylist=set(SCHEDULED_JOB_UNATTENDED_DENYLIST),
                skill_loading_mode=mode,
            )
            for mode in [SKILL_LOADING_INTERACTIVE, SKILL_LOADING_SCHEDULED]
        ]
        names = [{item["function"]["name"] for item in await toolkit.get_tool_schemas([])} for toolkit in toolkits]
        assert names[0] - {"Skill"} == names[1]
        assert not ({"Bash", "HttpRequest", "AskUser", "ScheduleJob", "ConfirmJob"} & names[1])
        for toolkit in toolkits:
            read = await toolkit.check_tool_available("Read", [])
            assert read.scope_dir == str(directory)
            assert read.extra_scopes == []
            with pytest.raises((ValueError, PermissionError)):
                read._resolve_file_path(skill.source_path)
        assert guardrails.network_enabled is False
        assert guardrails.permission_profile == permission_profile

    asyncio.run(run())


@pytest.mark.parametrize("name", ["Skill", "SkillViewer"])
def test_viewer_rejected_and_audited_at_tool_layer(project_job, fake_skill_models, name):
    """旧缓存和异常模型调用均在原生可用性检查与直接工具执行处拒绝。"""

    _, _, skill, project = project_job

    async def run():
        agent = skill_agent(skill, Path(project["rootPath"]))
        with pytest.raises(SkillViewerUnavailableError):
            await agent.toolkit.check_tool_available(name, [])
        call = ToolCallBlock(id="viewer-call", name=name, input='{"skill":"daily-report"}')
        results = [item async for item in agent.toolkit.call_tool(call, agent.state)]
        assert results[-1].state == "error"
        assert "SkillViewerUnavailable" in str(results)
        assert agent.toolkit.rejected_viewer_calls[-1]["requestedSkill"] == "daily-report"
        assert agent.toolkit.rejected_viewer_calls[-1]["toolCallId"] == "viewer-call"
        assert not fake_skill_models[-1].requests

    asyncio.run(run())


def test_mid_preload_version_change_fails_before_reply(project_job, fake_skill_models):
    """preflight 后到模型入口之间发生文件变化同样 fail closed。"""

    service, job, skill, project = project_job
    job = service.confirm(job.id)

    async def run():
        ctx, _, _, directory = await scheduled_context(service, job, project)
        agent = skill_agent(skill, directory)
        await prepare_scheduled_first_turn(agent, ctx, run_snapshot=None)
        path = Path(skill.source_path)
        path.write_text(path.read_text() + "\n新版本规则。\n")
        with pytest.raises(RuntimeError, match="skill_(version_changed|catalog_stale)"):
            await agent.reply(UserMsg(name="user", content="生成日报"))
        assert not fake_skill_models[-1].requests
        assert agent._aw_skill_context["validation"] == "failed"

    asyncio.run(run())
