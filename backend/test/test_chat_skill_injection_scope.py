"""真实文件目录验证共享只影响合格 Chat，不扩大 Project/子 Runtime 授权。"""
from types import SimpleNamespace

import pytest

from src.catalog.skill_seed import seed_system_skills
from src.skills import current_skill_context, build_skill_catalog
from src.skills.policy import set_skill_policy
from src.skills.service import list_catalog_skills
from src.skills.registry import register_user_skill, SkillRegisterRequest
from src.server.routes.chat.run_context import _merge_file_catalog_skills, _resolve_frozen_runtime_skills
from src.server.routes.chat.prompts import _build_skill_context_prompt


@pytest.fixture
def context(tmp_path):
    seed_system_skills()
    return current_skill_context()


def merge(context, configured=(), inject=True):
    skills = list_catalog_skills(build_skill_catalog(context))
    return _merge_file_catalog_skills([], external_skills=skills, inject=inject, configured_skill_ids=list(configured))


def test_agent_without_binding_gets_shared_system_packs(context):
    assert {item.id for item in merge(context)} == {"yinli-catalog", "yinli-charts", "word-documents"}


def test_unshared_system_requires_agent_configuration(context):
    for key in ("word-documents", "yinli-charts"):
        set_skill_policy(context.policy_path, key, availableToAllAgents=False)
    assert [item.id for item in merge(context)] == ["yinli-catalog"]
    assert {item.id for item in merge(context, ["word-documents"])} == {"yinli-catalog", "word-documents"}


def test_project_skill_directory_is_ignored(context, tmp_path):
    directory = tmp_path / ".agents/skills/project-skill"
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text("---\ndescription: 项目技能\n---\n执行")
    assert "project-skill" not in {item.name for item in build_skill_catalog(context).candidates}
    assert "project-skill" not in {skill.name for skill in merge(context)}


def test_disabled_skill_and_shadowed_skill_not_auto_added(context):
    set_skill_policy(context.policy_path, "word-documents", enabled=False)
    assert "word-documents" not in {skill.id for skill in merge(context)}


def test_shared_workspace_skill_joins_catalog(context):
    item = register_user_skill(SkillRegisterRequest(name="shared-office", description="办公"))
    assert item.id in {skill.id for skill in merge(context)}


def test_team_member_path_does_not_inject_shared(context):
    assert merge(context, ["yinli-charts"], inject=False) == []


def test_frozen_job_empty_does_not_append_shared(context):
    assert _resolve_frozen_runtime_skills([], agent_config=SimpleNamespace(skills=[]), project_resources=SimpleNamespace(filter_ids=lambda _kind, ids: ids)) == []


def test_skill_prompt_is_progressive():
    text = _build_skill_context_prompt()
    assert "Only Skill names and descriptions are listed up front" in text
    assert "<activated-skill>" in text
