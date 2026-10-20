from pathlib import Path

from src.server.routes.skills import discover_external_skills, resolve_runtime_skill_paths
from src.subagents.registry import load_subagent_catalog


def test_discovers_project_external_skill_and_subagent(tmp_path: Path):
    skill_dir = tmp_path / ".claude" / "skills" / "writer"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: writer\ndescription: Write documents.\n---\n\nUse the skill.\n",
        encoding="utf-8",
    )
    agent_dir = tmp_path / ".agent" / "agents"
    agent_dir.mkdir(parents=True)
    (agent_dir / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Review files.\nskills: [writer]\n---\n\nReview.\n",
        encoding="utf-8",
    )

    skills = discover_external_skills(
        tmp_path,
        user_data_root=tmp_path / "user",
        user_home_root=tmp_path / "home",
    )
    assert len(skills) == 1
    assert skills[0].enabled is True
    assert skills[0].source_scope == "project"
    assert resolve_runtime_skill_paths(skills, runtime_context=type("Context", (), {"project_root": tmp_path})()) == [
        str(skill_dir.resolve()),
    ]

    catalog = load_subagent_catalog(
        workspace_dir=str(tmp_path),
        user_dir=str(tmp_path / "user"),
        user_home_dir=tmp_path / "home",
    )
    assert catalog.definitions["reviewer"].path.endswith(".agent/agents/reviewer.md")


def test_user_external_skill_is_visible_but_disabled(tmp_path: Path):
    root = tmp_path / ".agents" / "skills" / "user-skill"
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text(
        "---\nname: user-skill\ndescription: User skill.\n---\n",
        encoding="utf-8",
    )
    skills = discover_external_skills(tmp_path / "project", user_data_root=tmp_path, user_home_root=tmp_path / "home")
    assert len(skills) == 1
    assert skills[0].enabled is False


def test_home_skills_stay_unloaded_until_scan_root_registered(tmp_path: Path):
    from src.catalog.skills import clear_extra_skill_scan_roots, register_skill_scan_root

    home_skill = tmp_path / ".agents" / "skills" / "home-skill"
    home_skill.mkdir(parents=True)
    (home_skill / "SKILL.md").write_text(
        "---\nname: home-skill\ndescription: Shared user skill.\n---\n",
        encoding="utf-8",
    )
    skills = discover_external_skills(
        tmp_path / "project",
        user_data_root=tmp_path / "user-data",
        user_home_root=tmp_path,
    )
    assert all(skill.name != "home-skill" for skill in skills)

    register_skill_scan_root(home_skill.parent)
    try:
        skills = discover_external_skills(
            tmp_path / "project",
            user_data_root=tmp_path / "user-data",
            user_home_root=tmp_path,
        )
        matched = [skill for skill in skills if skill.name == "home-skill"]
        assert len(matched) == 1
        assert matched[0].resource_path == str(home_skill.resolve())
    finally:
        clear_extra_skill_scan_roots()
