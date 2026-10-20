"""scheduled Job required Skill manifest 与 hash 校验。"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.scheduler.firing import _skill_context_manifest
from src.scheduler.models import JobRequiredSkill, ScheduledJobRun
from src.scheduler.skill_manifest import (
    SkillManifestError,
    build_required_skill_manifest,
    canonical_skill_ids,
    validate_required_skill_manifest,
)
from src.server.routes.scheduled_jobs import serialize_firing
from src.skills.registry import Skill, resolve_project_skill


@pytest.fixture()
def project_job(tmp_path):
    """真实统一目录包与 Job Agent，不向旧数据库登记 Skill。"""

    from src.scheduler.service import ScheduledJobService
    from src.scheduler.settings import set_scheduled_jobs_settings
    from src.skills import current_skill_context
    from src.storage.database import create_project, ensure_db, upsert_agent, upsert_project_resource_binding

    ensure_db()
    root = tmp_path / "project"
    root.mkdir()
    skill_dir = current_skill_context().tenant_root / "skills" / "daily-report"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: daily-report\ndescription: 生成日报\n---\n\n# 日报执行规则\n必须写入 outputs/report.md。\n",
        encoding="utf-8",
    )
    project = create_project({
        "id": "report-project", "workspaceId": "default", "name": "日报项目",
        "rootPath": str(root),
    })
    upsert_agent({
        "id": "report-agent", "name": "日报执行者", "role": "job",
        "workspaceId": "default", "system_prompt": "执行定时日报",
        "guardrails": {"filesystem_scope": str(root)},
        "jobToolkit": {"required": [{"kind": "builtin", "id": "Bash"}]},
    })
    set_scheduled_jobs_settings(enabled=True, capability_gate=True)
    skill = resolve_project_skill(root, "daily-report")
    assert skill is not None
    upsert_project_resource_binding({
        "project_id": project["id"],
        "resource_type": "skill",
        "resource_id": skill.id,
        "enabled": True,
    })
    service = ScheduledJobService()
    job = service.create_job(
        workspace_id="default", project_id=project["id"], name="每日日报",
        agent_id="report-agent", frozen_prompt="生成日报",
        task_intent="生成日报并写入项目文件",
        schedule_kind="daily", schedule_expr={"at": "09:00"}, timezone="Asia/Shanghai",
        skill_ids=[skill.id], capability_contract={"verdict": "generate_only"},
    )
    return service, job, skill, project


@pytest.mark.parametrize("use_name", [False, True])
def test_confirm_real_managed_skill_without_registry_row(project_job, use_name):
    """确认冻结统一目录包，不能 active 却返回空 manifest。"""

    from src.skills.registry import get_installed_skill
    from src.storage.database import list_project_resource_bindings

    service, job, skill, project = project_job
    assert get_installed_skill(skill.id) is not None
    assert {item["resourceId"] for item in list_project_resource_bindings(project["id"])} == {skill.id}
    if use_name:
        job = service.update_job(job.id, {"skill_ids": [skill.name]})
    active = service.confirm(job.id)
    assert active.status == "active"
    assert len(active.required_skills) == 1
    assert active.required_skills[0].content_sha256 == hashlib.sha256(
        Path(skill.source_path).read_bytes(),
    ).hexdigest()
    assert service.get_job(job.id).required_skills == active.required_skills


@pytest.mark.parametrize("use_name", [False, True])
def test_confirmed_managed_skill_reaches_scheduled_context(project_job, use_name):
    """走真实 Context 装配，正文首轮可用，且不进入 system prompt。"""

    from src.runtime_context import resolve_runtime_context
    from src.project_files import project_job_run_directory
    from src.server.routes.chat.models import ChatMessage
    from src.server.routes.chat.run_context import build_scheduled_run_context

    service, job, skill, project = project_job
    if use_name:
        job = service.update_job(job.id, {"skill_ids": [skill.name]})
    job = service.confirm(job.id)
    firing = service.run_now(job.id)
    data = ChatMessage(
        session_id="sched-project-report", project_id=project["id"],
        agent_id=job.agent_id, message=job.frozen_prompt,
    )
    context = resolve_runtime_context(
        project_id=project["id"], session_id=data.session_id, run_id="run-report",
        agent_id=job.agent_id,
    )
    ctx = asyncio.run(build_scheduled_run_context(
        job, firing, run_data=data, runtime_context=context,
        session_workspace_dir=str(project_job_run_directory(context.project_root, job.id, firing.id)),
    ))
    assert len(ctx.forced_skills) == 1
    assert "# 日报执行规则" in ctx.forced_skills[0].content
    assert "# 日报执行规则" not in ctx.system_prompt
    assert ctx.skill_paths == [skill.resource_path]
    assert ctx.skill_context_audit["validation"] == "passed"


def test_frozen_managed_skill_content_change_reports_version_error(project_job):
    """external ID 随内容变化时仍按冻结包位置诊断为版本漂移。"""

    service, job, skill, project = project_job
    job = service.confirm(job.id)
    Path(skill.source_path).write_text(
        "---\nname: daily-report\ndescription: 生成日报\n---\n\n# 新版本规则\n",
        encoding="utf-8",
    )
    with pytest.raises(SkillManifestError) as error:
        validate_required_skill_manifest(
            job.required_skills, canonical_ids=job.skill_ids,
            project_root=Path(project["rootPath"]),
        )
    assert error.value.code == "skill_version_changed"


def test_disabled_managed_skill_cannot_confirm(project_job):
    """Project binding 不代表可以越过停用状态。"""

    from src.skills.policy import set_skill_policy
    from src.scheduler.models import ScheduledJobStateError

    service, job, skill, _ = project_job
    set_skill_policy(skill._catalog_context.policy_path, skill.id, enabled=False)
    with pytest.raises(ScheduledJobStateError):
        service.confirm(job.id)
    assert service.get_job(job.id).status == "pending_confirmation"


def test_firing_preflight_resolves_frozen_managed_skill(project_job, monkeypatch):
    """桥接预检解析统一目录，且不启动真实模型。"""

    from src.scheduler.chat_bridge import start_scheduled_chat_run
    from src.scheduler.firing import _build_request

    service, job, _, _ = project_job
    active = service.confirm(job.id)
    firing = service.run_now(job.id)
    launched = []

    async def capture_launch(**kwargs):
        launched.append(kwargs)
        return "project-skill-run"

    monkeypatch.setattr("src.scheduler.chat_bridge.launch_scheduled_chat_run", capture_launch)
    result = asyncio.run(start_scheduled_chat_run(_build_request(active, firing)))
    assert result == "project-skill-run"
    assert launched[0]["required_skills"][0]["contentSha256"] == active.required_skills[0].content_sha256


def test_frozen_path_is_independent_of_project_root(project_job, tmp_path):
    """统一 Skill 来源不随 Project root 改变。"""

    service, job, _, _ = project_job
    active = service.confirm(job.id)
    other_root = tmp_path / "other-project"
    other_root.mkdir()
    skills, _, _ = validate_required_skill_manifest(
        active.required_skills, canonical_ids=active.skill_ids,
        project_root=other_root,
    )
    assert [item.id for item in skills] == active.skill_ids


def _skill(skill_id: str, path: str) -> Skill:
    from src.skills.service import catalog_for, get_catalog_skill
    return get_catalog_skill(skill_id, catalog_for())


def test_canonical_skill_ids_job_order_then_contract() -> None:
    assert canonical_skill_ids(["job-a", "shared"], ["shared", "contract-b", "job-a"]) == [
        "job-a", "shared", "contract-b",
    ]


def test_build_manifest_uses_raw_skill_md_hash(monkeypatch, tmp_path) -> None:
    from src.skills import current_skill_context
    skill_dir = current_skill_context().tenant_root / "skills/daily-report"
    skill_dir.mkdir(parents=True)
    raw = b"# Daily report\n\nKeep the trailing space. \n"
    (skill_dir / "SKILL.md").write_bytes(raw)
    skill = _skill("daily-report", str(skill_dir))

    ids, manifest, live = build_required_skill_manifest(
        job_skill_ids=[],
        contract_skill_ids=["daily-report"],
    )

    assert ids == [manifest[0].skill_id]
    assert ids[0].startswith("skill-")
    assert manifest[0].content_sha256 == hashlib.sha256(raw).hexdigest()
    assert live["daily-report"].markdown.endswith(" \n")
    assert manifest[0].content_chars == len(raw.decode("utf-8"))


def test_project_directory_skill_is_not_a_catalog_source(tmp_path) -> None:
    skill_dir = tmp_path / ".agents" / "skills" / "daily-report"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: daily-report\ndescription: 生成日报\n---\n\n# Daily report\n",
        encoding="utf-8",
    )
    assert resolve_project_skill(tmp_path, "daily-report") is None
    with pytest.raises(SkillManifestError) as error:
        build_required_skill_manifest(job_skill_ids=["daily-report"], project_root=tmp_path)
    assert error.value.code == "skill_missing"


def test_managed_directory_skill_can_be_frozen_by_name(tmp_path) -> None:
    from src.skills import current_skill_context
    skill_dir = current_skill_context().tenant_root / "skills" / "daily-report"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: daily-report\ndescription: 生成日报\n---\n\n# Daily report\n",
        encoding="utf-8",
    )

    ids, manifest, _ = build_required_skill_manifest(
        job_skill_ids=["daily-report"],
        project_root=tmp_path,
    )

    assert ids == [manifest[0].skill_id]
    assert ids[0].startswith("skill-")
    assert manifest[0].source_path.endswith("/skills/daily-report/SKILL.md")


def test_validate_manifest_rejects_skill_version_change(monkeypatch, tmp_path) -> None:
    from src.skills import current_skill_context
    skill_dir = current_skill_context().tenant_root / "skills/report"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# v1\n\nversion one", encoding="utf-8")
    skill = _skill("report", str(skill_dir))
    _, manifest, _ = build_required_skill_manifest(job_skill_ids=["report"])
    (skill_dir / "SKILL.md").write_text("# v2\n\nversion two", encoding="utf-8")

    with pytest.raises(SkillManifestError, match="已变化") as error:
        validate_required_skill_manifest(manifest, canonical_ids=["report"])
    assert error.value.code == "skill_version_changed"


def test_validate_manifest_requires_project_binding(monkeypatch, tmp_path) -> None:
    from src.skills import current_skill_context
    skill_dir = current_skill_context().tenant_root / "skills/bound"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# bound\n\nBound instructions", encoding="utf-8")
    skill = _skill("bound", str(skill_dir))
    _, manifest, _ = build_required_skill_manifest(job_skill_ids=["bound"])
    resources = SimpleNamespace(filter_ids=lambda _kind, _ids: [])

    with pytest.raises(SkillManifestError) as error:
        validate_required_skill_manifest(manifest, canonical_ids=["bound"], project_resources=resources)
    assert error.value.code == "skill_unbound"


def test_required_skill_model_accepts_storage_aliases() -> None:
    record = JobRequiredSkill.model_validate({
        "skillId": "report",
        "contentSha256": "abc",
        "contentChars": 10,
        "estimatedTokens": 3,
        "frozenAt": "2026-10-16T00:00:00+00:00",
    })
    assert record.skill_id == "report"
    assert record.model_dump(by_alias=True)["contentSha256"] == "abc"


def test_firing_skill_context_audit_is_serializable_without_body() -> None:
    required = JobRequiredSkill(
        skill_id="report",
        content_sha256="abc",
        content_chars=12,
        estimated_tokens=3,
        frozen_at="2026-10-16T00:00:00+00:00",
    )
    job = SimpleNamespace(required_skills=[required], context_policy="fresh")
    audit = _skill_context_manifest(job, validation="failed")
    audit["errorCode"] = "skill_version_changed"
    firing = ScheduledJobRun(
        id="firing-audit",
        job_id="job-audit",
        workspace_id="default",
        project_id="default",
        trigger="manual",
        scheduled_for="2026-10-16T00:00:00+00:00",
        status="failed",
        output_manifest={"skillContext": audit},
        created_at="2026-10-16T00:00:00+00:00",
        updated_at="2026-10-16T00:00:00+00:00",
    )
    payload = serialize_firing(firing)
    assert payload["outputManifest"]["skillContext"]["errorCode"] == "skill_version_changed"
    assert "markdown" not in payload["outputManifest"]["skillContext"]["items"][0]
