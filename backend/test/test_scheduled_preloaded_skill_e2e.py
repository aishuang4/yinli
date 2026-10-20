"""日报定时任务确认后两次 firing 的真实首轮模型载荷回归测试。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from agentscope.message import ToolCallBlock, UserMsg
from agentscope.model import ChatResponse

from src.agents.runtime_types import SKILL_LOADING_INTERACTIVE
from src.scheduler.firing import _skill_context_manifest
from src.server.routes.chat.react_runtime import execute_react_runtime
from src.server.routes.chat.scheduled_skill_preload import pop_scheduled_skill_audit
from src.tools.scheduled_job_tools import ConfirmJob
from test.test_scheduled_job_skill_context import project_job as project_job  # noqa: PLC0414 - 复用 pytest fixture
from test.test_scheduled_preloaded_skill_runtime import (
    fake_skill_models as fake_skill_models,  # noqa: PLC0414 - 复用 pytest fixture
)
from test.test_scheduled_preloaded_skill_runtime import (
    request_text,
    scheduled_context,
    skill_agent,
)


def test_daily_job_two_firings_keep_frozen_first_turn_and_chat_on_demand(project_job, fake_skill_models):
    """真实 ConfirmJob→两次独立 React runtime→模型，普通 Chat 仍有按需 Skill viewer。"""

    service, job, skill, project = project_job
    job = service.update_job(job.id, {"schedule_kind": "daily", "schedule_expr": {"at": "09:00"}})
    confirm = ConfirmJob(workspace_id="default", project_id=project["id"], agent_id=job.agent_id, session_id="")
    confirm._service = service
    chunk = asyncio.run(confirm(job_id=job.id))
    payload = json.loads(chunk.content[0].text)
    assert payload["skillPreload"] == "first_model_call"
    job = service.get_job(job.id)

    async def run():
        first_turns = []
        for suffix in ["first", "second"]:
            ctx, data, firing, _ = await scheduled_context(service, job, project, suffix=suffix)
            before = len(fake_skill_models)
            events = [event async for event in execute_react_runtime(ctx=ctx, run_data=data)]
            assert events
            models = fake_skill_models[before:]
            assert models and models[0].requests
            request = models[0].requests[0]
            text = request_text(request)
            assert "# 日报执行规则" in text
            assert "activated-skill" in text
            assert "call the Skill tool" not in text
            assert "Read a Skill's full instructions with the Skill tool" not in text
            assert job.required_skills[0].content_sha256 in text
            assert "# 日报执行规则" not in request_text(request, role="system")
            assert not ({"Skill", "SkillViewer"} & {
                item["function"]["name"] for item in request["tools"] or []
            })
            first_turns.append(ctx.forced_skills[0].content)
            audit = pop_scheduled_skill_audit(firing.id)
            assert audit["contextVerification"]["status"] == "passed"
            assert audit["injectionOrder"] == [skill.id]
            manifest = _skill_context_manifest(
                job, validation="passed", owner_session_id=ctx.session_id,
                firing_run_id=ctx.runtime_context.run_id, runtime=audit,
            )
            assert manifest["runtime"]["injectionOrder"] == [skill.id]
            assert "# 日报执行规则" not in json.dumps(manifest, ensure_ascii=False)
            service.complete_firing(firing.id, "succeeded", output_manifest={"skillContext": manifest})
            assert service.get_firing(firing.id).output_manifest["skillContext"] == manifest
        assert first_turns[0] == first_turns[1]

        interactive = skill_agent(
            skill, Path(project["rootPath"]), mode=SKILL_LOADING_INTERACTIVE, agent_id="normal-chat",
        )
        await interactive.reply(UserMsg(name="user", content="普通交互对话"))
        request = fake_skill_models[-1].requests[0]
        assert "Skill" in {item["function"]["name"] for item in request["tools"] or []}
        assert "# 日报执行规则" not in request_text(request)
        chunks = [
            item async for item in interactive.toolkit.call_tool(
                ToolCallBlock(
                    id="ordinary-skill", name="Skill", input=json.dumps({"skill": skill.load_name}),
                ),
                interactive.state,
            )
        ]
        assert "# 日报执行规则" in str(chunks)

        path = Path(skill.source_path)
        path.write_text(path.read_text() + "\n变更后的 Skill。\n")
        before = sum(len(model.requests) for model in fake_skill_models)
        with pytest.raises(ValueError, match="skill_version_changed"):
            await scheduled_context(service, job, project, suffix="changed")
        assert sum(len(model.requests) for model in fake_skill_models) == before

    asyncio.run(run())


def test_abnormal_model_viewer_call_is_rejected_and_recorded(project_job, fake_skill_models, monkeypatch):
    """模型真实返回 Skill 调用：AgentScope 首道工具检查拒绝，Run 审计记录同一 toolCallId。"""

    from src.server.routes.chat import scheduled_skill_preload as preload
    from test.test_scheduled_preloaded_skill_runtime import RecordingSkillModel

    original = RecordingSkillModel._call_api
    activate = preload.activate_forced_skills
    activations = []

    def count_activation(agent, skills):
        activations.append([item.id for item in skills])
        return activate(agent, skills)

    async def hallucinate(self, model, **kwargs):
        response = await original(self, model, **kwargs)
        if len(self.requests) == 1:
            return ChatResponse(
                content=[ToolCallBlock(id="stale-viewer", name="Skill", input='{"skill":"daily-report"}')],
                is_last=True,
            )
        return response

    monkeypatch.setattr(RecordingSkillModel, "_call_api", hallucinate)
    monkeypatch.setattr(preload, "activate_forced_skills", count_activation)
    service, job, skill, project = project_job
    job = service.confirm(job.id)

    async def run():
        ctx, data, firing, _ = await scheduled_context(service, job, project)
        events = [event async for event in execute_react_runtime(ctx=ctx, run_data=data)]
        assert events
        assert len(fake_skill_models[-1].requests) == 2
        assert "SkillViewerUnavailable" in request_text(fake_skill_models[-1].requests[1])
        audit = pop_scheduled_skill_audit(firing.id)
        rejected = audit["rejectedViewerCalls"]
        assert rejected == [{
            **rejected[0], "toolCallId": "stale-viewer", "toolName": "Skill",
            "requestedSkill": "daily-report", "code": "skill_viewer_unavailable",
        }]
        assert activations == [[skill.id]]

    asyncio.run(run())
