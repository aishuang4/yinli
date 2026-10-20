"""定时任务 Skill ID 选择、服务端冻结与旧客户端兼容测试。"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from src.scheduler.skill_manifest import SkillManifestError, resolve_job_skill_refs
from src.tools.scheduled_job_tools import ConfirmJob, ScheduleJob
from test.test_scheduled_job_skill_context import project_job as project_job  # noqa: PLC0414 - 复用 pytest fixture


def response_body(chunk):
    """读取工具返回的 JSON 文本。"""

    return json.loads(chunk.content[0].text)


def test_skill_schema_exposes_only_ids_and_resolves_unique_names(project_job):
    """模型只填写稳定 ID；旧唯一名称可解析，错误及路径不会静默丢弃。"""

    _, _, skill, project = project_job
    properties = ScheduleJob.input_schema["properties"]
    assert "required_skills" not in properties
    assert "<id>" in properties["skill_ids"]["description"]
    root = Path(project["rootPath"])
    assert resolve_job_skill_refs([skill.id, "daily-report"], project_root=root) == [skill.id]
    for ref in ["/tmp/daily-report", "~/skills/report", "C:\\skills\\report", "references/report", "SKILL.md"]:
        with pytest.raises(SkillManifestError) as error:
            resolve_job_skill_refs([ref], project_root=root)
        assert error.value.code == "skill_ref_invalid"
    with pytest.raises(SkillManifestError) as error:
        resolve_job_skill_refs(["not-installed"], project_root=root)
    assert error.value.code == "skill_missing"


def test_ambiguous_skill_names_are_not_guessed(project_job):
    """重名的两个文件包必须提示歧义，不因扫描顺序择一。"""

    from src.skills import current_skill_context

    _, _, _, project = project_job
    target = current_skill_context().tenant_root / "skills" / "another-daily"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("---\nname: daily-report\ndescription: 第二份日报\n---\n\n另一个包。\n")
    with pytest.raises(SkillManifestError) as error:
        resolve_job_skill_refs(["daily-report"], project_root=Path(project["rootPath"]))
    assert error.value.code == "skill_ambiguous"


def test_manifest_is_server_owned_and_old_roundtrip_is_readonly(project_job):
    """创建与更新的伪造 hash 均不能覆盖服务端冻结快照，旧回传不重新确认。"""

    service, job, skill, project = project_job
    forged = [{"skillId": skill.id, "contentSha256": "forged"}]
    created = service.create_job(
        workspace_id="default", project_id=project["id"], name="兼容旧客户端",
        agent_id=job.agent_id, frozen_prompt="生成日报", task_intent="生成日报",
        schedule_kind="daily", schedule_expr={"at": "09:00"}, timezone="Asia/Shanghai",
        skill_ids=[skill.id], required_skills=forged,
        capability_contract={"verdict": "generate_only"},
    )
    assert created.required_skills == []
    active = service.confirm(created.id)
    original = active.required_skills
    assert original[0].content_sha256 == hashlib.sha256(Path(skill.source_path).read_bytes()).hexdigest()
    for manifest in [forged, [item.model_dump(by_alias=True) for item in original]]:
        returned = service.update_job(active.id, {"required_skills": manifest})
        assert returned.status == "active"
        assert returned.required_skills == original
    assert service.confirm(active.id).required_skills == original


def test_confirm_tool_returns_frozen_ids_hash_and_owner_grant(project_job):
    """确认工具直接回显实际冻结 ID/hash、owner grant 版本与首轮预加载状态。"""

    from src.scheduler.session_grant_check import owner_session_id
    from src.sessions.records import get_session

    service, job, skill, project = project_job
    tool = ConfirmJob(
        workspace_id="default", project_id=project["id"], agent_id=job.agent_id, session_id="",
    )
    tool._service = service
    body = response_body(asyncio.run(tool(job_id=job.id)))
    assert body["skillPreload"] == "first_model_call"
    assert "首次模型调用前自动预加载" in body["message"]
    frozen = body["requiredSkills"][0]
    assert frozen["skillId"] == skill.id
    assert frozen["contentSha256"] == hashlib.sha256(Path(skill.source_path).read_bytes()).hexdigest()
    assert frozen["frozenAt"]
    owner = owner_session_id(job.id)
    assert body["ownerSessionId"] == owner
    session = get_session(owner) if owner else None
    assert body["ownerGrantVersion"] == (session["resourceGrantVersion"] if session else None)
