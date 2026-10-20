"""统一 Skill 根的文件权威、策略并发和快照回归。"""
import hashlib
import multiprocessing
from pathlib import Path

import pytest

from src.skills import SkillContext, SkillError, build_skill_catalog, read_skill, resolve_skill
from src.skills.policy import read_policy, remove_skill_policy, set_skill_policy, update_policy
from src.skills.service import list_catalog_skills


def package(root, relative="report", name="report", body="执行日报"):
    directory = root / relative
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: 日报说明\n---\n{body}\n",
        encoding="utf-8",
    )
    return directory


@pytest.fixture
def context(tmp_path):
    return SkillContext(
        tenant_root=tmp_path / "tenant",
        project_root=tmp_path / "project",
        project_id="p1",
    )


def managed_package(context, relative="report", **kwargs):
    return package(context.tenant_root / "skills", relative, **kwargs)


def test_file_authority_identity_and_stale_read(context):
    path = managed_package(context)
    first = build_skill_catalog(context)
    selected = resolve_skill("report", first)
    assert "执行日报" not in first.model_dump_json()
    old = selected.candidate
    (path / "SKILL.md").write_text("---\nname: report\ndescription: 新描述\n---\n新正文")
    new = resolve_skill("report", build_skill_catalog(context))
    assert new.candidate.id == old.id and new.candidate.content_hash != old.content_hash
    with pytest.raises(SkillError) as stale:
        read_skill(selected)
    assert stale.value.code == "skill_catalog_stale"
    with pytest.raises(SkillError) as version:
        read_skill(new, expected_hash=old.content_hash)
    assert version.value.code == "skill_version_changed"
    (path / "SKILL.md").unlink()
    with pytest.raises(SkillError) as missing:
        read_skill(selected)
    assert missing.value.code == "skill_missing"
    assert not build_skill_catalog(context).candidates


def test_only_canonical_root_is_scanned(context):
    central = managed_package(context, body="中央版本")
    package(context.project_root / ".agents/skills", body="项目版本")
    package(context.project_root / ".claude/skills", body="兼容项目版本")
    home = context.tenant_root.parent / "home"
    package(home / ".agents/skills", body="HOME 版本")
    catalog = build_skill_catalog(context.model_copy(update={"home_root": home}))
    assert len(catalog.roots) == 1
    assert catalog.roots[0].path == (context.tenant_root / "skills").resolve()
    assert len(catalog.candidates) == 1
    selected = resolve_skill("report", catalog)
    assert selected.candidate.package_path == central.resolve()
    assert "中央版本" in read_skill(selected).markdown


def test_same_root_duplicate_name_is_ambiguous(context):
    a = managed_package(context, "a")
    b = managed_package(context, "b")
    (a / "script.py").write_text("a")
    (b / "script.py").write_text("b")
    catalog = build_skill_catalog(context)
    assert len(catalog.candidates) == 2
    assert len({item.id for item in catalog.candidates}) == 2
    with pytest.raises(SkillError) as error:
        resolve_skill("report", catalog)
    assert error.value.code == "skill_ambiguous"
    for item in catalog.candidates:
        assert resolve_skill(item.load_name, catalog).candidate == item


def test_recursive_boundary_and_symlink_safety(context):
    root = context.tenant_root / "skills"
    path = managed_package(context, "category/report")
    package(path / "references", "hidden", name="hidden")
    package(root / "node_modules", "hidden", name="hidden")
    (root / "z-alias").symlink_to(path, target_is_directory=True)
    (root / "loop").symlink_to(root, target_is_directory=True)
    outside = package(context.tenant_root, "outside", name="outside")
    (root / "outside").symlink_to(outside, target_is_directory=True)
    catalog = build_skill_catalog(context)
    assert len(catalog.candidates) == 1
    assert catalog.candidates[0].relative_path == "category/report"
    assert any(d.code == "skill_scope_denied" for d in catalog.diagnostics)


@pytest.mark.parametrize("raw,valid", [
    (b"\xef\xbb\xbf---\ndescription: description\n---\nbody", True),
    (b"# Header\n\nFirst body paragraph", True),
    (b"---\nname: [invalid\n---\nbody", False),
    (b"---\n- invalid\n---\nbody", False),
    (b"\xff", False),
    (b"# Only header", False),
])
def test_single_parser_defaults_and_invalid(context, raw, valid):
    path = context.tenant_root / "skills/directory-name"
    path.mkdir(parents=True)
    (path / "SKILL.md").write_bytes(raw)
    catalog = build_skill_catalog(context)
    item = catalog.candidates[0]
    assert item.content_hash == hashlib.sha256(raw).hexdigest()
    if valid:
        assert item.name == "directory-name"
        assert read_skill(resolve_skill(item.id, catalog)).markdown == raw.decode("utf-8-sig")
    else:
        assert item.state == "invalid"
        with pytest.raises(SkillError):
            resolve_skill(item.id, catalog)


def test_policy_stable_after_edit_and_delete_cleanup(context):
    path = managed_package(context)
    old = build_skill_catalog(context).candidates[0]
    update_policy(
        context.policy_path,
        lambda policy: policy.legacy_aliases.update({"old-report": [old.id]}),
    )
    set_skill_policy(context.policy_path, old.id, enabled=False, availableToAllAgents=False)
    (path / "SKILL.md").write_text("---\ndescription: updated\n---\nbody")
    catalog = build_skill_catalog(context)
    assert catalog.candidates[0].id == old.id
    assert not catalog.candidates[0].enabled and not catalog.candidates[0].shared
    remove_skill_policy(context.policy_path, old.id, relative_path=old.relative_path)
    policy = read_policy(context.policy_path)
    assert old.id not in policy.overrides and "old-report" not in policy.legacy_aliases


def _child_update(path, key):
    set_skill_policy(Path(path), key, enabled=False)


def test_cross_process_policy_transaction(context):
    processes = [multiprocessing.get_context("spawn").Process(
        target=_child_update,
        args=(str(context.policy_path), str(i)),
    ) for i in range(4)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(20)
        assert process.exitcode == 0
    policy = read_policy(context.policy_path)
    assert policy.version == 4 and len(policy.overrides) == 4
    with pytest.raises(SkillError) as error:
        update_policy(context.policy_path, lambda policy: None, expected_version=0)
    assert error.value.code == "skill_policy_conflict"


def test_atomic_failure_preserves_previous_policy(context, monkeypatch):
    set_skill_policy(context.policy_path, "a", enabled=False)
    before = context.policy_path.read_bytes()

    def fail(*_args):
        raise OSError("故障注入")

    monkeypatch.setattr("src.skills.policy.os.replace", fail)
    with pytest.raises(OSError):
        set_skill_policy(context.policy_path, "b", enabled=True)
    assert context.policy_path.read_bytes() == before
    assert not list(context.policy_path.parent.glob("*.tmp"))


def test_managed_packages_need_no_database_or_owner_record(context):
    managed_package(context, "managed")
    item = resolve_skill("managed", build_skill_catalog(context)).candidate
    assert item.root.managed and item.root.kind == "managed" and item.root.scope == "tenant"


def test_rename_changes_identity(context):
    path = managed_package(context)
    original = build_skill_catalog(context).candidates[0]
    path.rename(path.with_name("renamed"))
    assert build_skill_catalog(context).candidates[0].id != original.id
    with pytest.raises(SkillError):
        resolve_skill(original.id, build_skill_catalog(context))


def test_depth_limit_reserved_identity_and_collision(context, monkeypatch):
    monkeypatch.setattr("src.skills.catalog.MAX_DEPTH", 1)
    managed_package(context, "a/b/c")
    managed_package(context, "reserved", name="yinli-catalog")
    catalog = build_skill_catalog(context)
    assert any(item.code == "skill_scan_limit" for item in catalog.diagnostics)
    assert catalog.candidates[0].error_code == "skill_reserved_name"
    managed_package(context, "a")
    monkeypatch.setattr("src.skills.catalog.stable_skill_id", lambda *_args: "skill-collision")
    with pytest.raises(SkillError) as error:
        build_skill_catalog(context)
    assert error.value.code == "skill_identity_collision"


def test_refresh_membership_and_support_signature(context):
    path = managed_package(context)
    first = build_skill_catalog(context)
    assert build_skill_catalog(context) is first
    (path / "script.py").write_text("1")
    second = build_skill_catalog(context)
    assert first.signature != second.signature
    assert first.candidates[0].content_hash == second.candidates[0].content_hash
    managed_package(context, "other", name="other")
    assert len(build_skill_catalog(context).candidates) == 2


def test_project_context_does_not_change_catalog_source(context, tmp_path, monkeypatch):
    managed_package(context)
    first = build_skill_catalog(context)
    monkeypatch.chdir(tmp_path)
    other = context.model_copy(update={"project_id": "p2", "project_root": tmp_path / "other-project"})
    second = build_skill_catalog(other)
    assert first.candidates[0].id == second.candidates[0].id
    assert first.candidates[0].package_path == second.candidates[0].package_path
    assert list_catalog_skills(second)[0].source_scope != "project"


def test_seed_reads_are_idempotent_and_protect_user_edits(tmp_path, monkeypatch):
    from src.catalog import skill_seed
    source, target = tmp_path / "bundled", tmp_path / "installed"
    for key in ("yinli-catalog", "yinli-charts", "word-documents"):
        package(source, key, name=key)
    monkeypatch.setattr(skill_seed, "_ARTIFACTS_SKILLS", source)
    monkeypatch.setattr(skill_seed, "skill_packages_root", lambda: target)
    skill_seed.seed_system_skills()
    manual = target / "word-documents/SKILL.md"
    initial = manual.stat().st_mtime_ns
    skill_seed.seed_system_skills()
    assert manual.stat().st_mtime_ns == initial
    manual.write_text("用户编辑后的 Word 包")
    (source / "word-documents/SKILL.md").write_text("新安装版本")
    skill_seed.seed_system_skills()
    assert manual.read_text() == "用户编辑后的 Word 包"
    assert list((target / ".upgrades/word-documents").glob("*/SKILL.md"))


def test_skills_api_and_job_models_accept_legacy_payloads_without_expansion():
    from src.skills.api_models import Skill
    from src.scheduler.models import JobRequiredSkill
    item = Skill(id="old", name="old", description="legacy", category="test", updatedAt="")
    assert item.load_name == "" and item.resolution_state == "resolved"
    assert not item.model_dump(by_alias=True)["shadowedBy"]
    frozen = JobRequiredSkill(skillId="old", contentSha256="unchanged")
    assert frozen.content_sha256 == "unchanged" and frozen.load_name == ""
