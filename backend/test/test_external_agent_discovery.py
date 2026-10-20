from pathlib import Path

from src.server.routes.skills import discover_external_skills
from src.subagents.registry import load_subagent_catalog


def test_project_skill_is_ignored_but_subagent_is_discovered(tmp_path: Path):
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
    assert skills == []

    catalog = load_subagent_catalog(
        workspace_dir=str(tmp_path),
        user_dir=str(tmp_path / "user"),
        user_home_dir=tmp_path / "home",
    )
    assert "reviewer" not in catalog.definitions


def test_tenant_compat_skill_is_not_part_of_application_catalog(tmp_path: Path):
    root = tmp_path / ".agents" / "skills" / "user-skill"
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text(
        "---\nname: user-skill\ndescription: User skill.\n---\n",
        encoding="utf-8",
    )
    skills = discover_external_skills(tmp_path / "project", user_data_root=tmp_path, user_home_root=tmp_path / "home")
    assert skills == []


def test_home_agents_skill_is_not_part_of_application_catalog(tmp_path: Path):
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
    assert skills == []
