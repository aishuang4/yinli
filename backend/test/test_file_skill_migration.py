"""历史租户迁移预检、故障重试、版本保留及回滚演练。"""
from pathlib import Path
import hashlib
import json

import pytest

from src.skills import SkillContext, build_skill_catalog, resolve_skill, read_skill
from src.skills.migration import migration_preflight, migrate_skill_files, restore_skill_backup
from src.skills.policy import read_policy


def record(id, path="", **overrides):
    value = {"id": id, "name": id, "description": "旧技能", "category": "test", "workspaceId": "default",
             "content": "数据库旧正文", "resourcePath": path, "status": "installed", "enabled": True, "availableToAllAgents": False}
    value.update(overrides)
    return value


def manual(path, text="磁盘当前正文"):
    path.mkdir(parents=True, exist_ok=True)
    (path / "SKILL.md").write_text("---\nname: report\ndescription: 日报\n---\n" + text)
    return path


@pytest.mark.parametrize("crash_stage", ["backed_up", "packages_written", "verified"])
def test_migration_retry_existing_files_win_and_complete_last(tmp_path, crash_stage):
    context = SkillContext(tenant_root=tmp_path / "tenant")
    existing = manual(context.tenant_root / "skills/existing")
    original = (existing / "SKILL.md").read_bytes()
    records = [record("old-id", str(existing)), record("body-only"), record("empty", content=""),
               record("unowned", workspaceId=""), record("lost-outside", str(tmp_path / "external-missing"))]
    before = migration_preflight([context], records)
    assert not context.policy_path.exists()  # 只读预检没有副作用。
    assert before["records"][3]["result"] == "unowned"
    def crash(stage):
        if stage == crash_stage:
            raise RuntimeError("中断注入")
    with pytest.raises(RuntimeError):
        migrate_skill_files([context], records, checkpoint_hook=crash)
    state = context.tenant_root / "data/skill-file-migration-v1"
    assert not (state / "complete.json").exists()
    report = migrate_skill_files([context], records)
    assert (state / "complete.json").exists()
    assert (existing / "SKILL.md").read_bytes() == original
    catalog = build_skill_catalog(context)
    assert "磁盘当前正文" in read_skill(resolve_skill("old-id", catalog)).markdown
    body = read_skill(resolve_skill("body-only", catalog))
    assert "数据库旧正文" in body.markdown
    assert not resolve_skill("old-id", catalog).candidate.shared
    assert len(list((context.tenant_root / "skills").glob("legacy-*/SKILL.md"))) == 1
    saved = context.policy_path.read_bytes()
    assert migrate_skill_files([context], records) == report
    assert context.policy_path.read_bytes() == saved
    assert json.loads((state / "backup/legacy.json").read_text())["records"] == records


def test_duplicate_hash_alias_stays_ambiguous_and_frozen_path_is_proven(tmp_path):
    context = SkillContext(tenant_root=tmp_path / "tenant", project_root=tmp_path / "project", project_id="p")
    a = manual(context.tenant_root / "skills/a")
    b = manual(context.tenant_root / "skills/b")
    old = "external-" + hashlib.sha256((a / "SKILL.md").read_bytes()).hexdigest()[:16]
    frozen_hash = "0" * 64
    jobs = [{"id": "frozen", "workspace_id": "default", "project_id": "p", "required_skills": [{
        "skillId": "old-unique", "sourcePath": str(a / "SKILL.md"), "contentSha256": frozen_hash}]}]
    report = migrate_skill_files([context], [], [{"id": old, "enabled": False}], evidence={"jobs": jobs})
    catalog = build_skill_catalog(context)
    from src.skills import SkillError
    with pytest.raises(SkillError) as error:
        resolve_skill(old, catalog)
    assert error.value.code == "skill_ambiguous"
    assert resolve_skill("old-unique", catalog).candidate.package_path == a.resolve()
    assert jobs[0]["required_skills"][0]["contentSha256"] == frozen_hash
    assert any(item["result"] == "ambiguous" for item in report["failures"])
    assert not (b / "skills-policy.json").exists()


def test_rollback_preserves_new_mode_files_before_restoring(tmp_path):
    context = SkillContext(tenant_root=tmp_path / "tenant")
    original = manual(context.tenant_root / "skills/original", "原始正文")
    profile = context.tenant_root / "workspaces/default/.agents/profiles/agent.md"
    profile.parent.mkdir(parents=True)
    profile.write_text("原始 profile")
    migrate_skill_files([context], [record("old", str(original))])
    created = manual(context.tenant_root / "skills/new", "迁移后新增")
    profile.write_text("迁移后修改")
    exported = restore_skill_backup(context.tenant_root)
    assert (exported / "skills/new/SKILL.md").is_file()
    assert (exported / "workspaces/default/.agents/profiles/agent.md").read_text() == "迁移后修改"
    assert profile.read_text() == "原始 profile"
    assert not created.exists()
    assert "原始正文" in (original / "SKILL.md").read_text()


def test_startup_migration_pauses_active_missing_job_preserves_hash_and_old_data(tmp_path, monkeypatch):
    from src.storage import database
    from src.scheduler.service import ScheduledJobService
    from src.scheduler.settings import set_scheduled_jobs_settings
    from src.skills.migration import migrate_tenant_skills
    from src.skills import current_skill_context
    database.ensure_db()
    project = database.create_project({"id": "legacy-project", "name": "历史项目", "rootPath": str(tmp_path / "legacy-project")})
    Path(project["rootPath"]).mkdir()
    database.upsert_agent({"id": "legacy-agent", "name": "历史 Job 执行者", "workspaceId": "default"})
    set_scheduled_jobs_settings(enabled=True, capability_gate=False)
    service = ScheduledJobService()
    job = service.create_job(workspace_id="default", project_id=project["id"], agent_id="legacy-agent", name="旧定时任务",
        frozen_prompt="输出日报", skill_ids=["missing-old"], schedule_kind="daily", schedule_expr={"at": "09:00"}, timezone="Asia/Shanghai")
    old_hash = "f" * 64
    # 模拟历史已确认任务，而非通过新确认路径制造非法状态。
    service._save_job(job.id, {"status": "active", "required_skills": [{"skillId": "missing-old", "contentSha256": old_hash}]})
    legacy = record("missing-old", content="")
    database.replace_skills({"missing-old": legacy})
    report = migrate_tenant_skills()
    updated = service.get_job(job.id)
    assert updated.status == "paused"
    assert updated.required_skills[0].content_sha256 == old_hash
    assert database.list_skills()[0]["id"] == "missing-old"
    assert report["records"][0]["result"] == "missing"
    context = current_skill_context()
    state = context.tenant_root / "data/skill-file-migration-v1"
    assert (state / "backup/database.sqlite").is_file()
    assert database.list_user_notifications()
    def forbidden(*args, **kwargs):
        raise AssertionError("完成迁移后不得再次读取旧登记表")
    monkeypatch.setattr(database, "list_skills", forbidden)
    migrate_tenant_skills()
    assert service.get_job(job.id).required_skills[0].content_sha256 == old_hash
