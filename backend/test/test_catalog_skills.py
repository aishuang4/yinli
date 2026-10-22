"""Skill 文件权威与项目目录默认可运行。"""

from pathlib import Path

import pytest

from src.catalog.skills import project_skill_refs, read_package_markdown
from src.server.routes.skills import SkillRegisterRequest, register_user_skill
from src.skills.registry import cached_skill_records


def test_register_user_skill_does_not_persist_body(tmp_path, monkeypatch) -> None:
    packages = tmp_path / "skills"
    monkeypatch.setattr("src.server.routes.skill_packages.skill_packages_root", lambda: packages)
    monkeypatch.setattr("src.skills.registry.skill_packages_root", lambda: packages)
    monkeypatch.setattr("src.catalog.skills.skill_packages_root", lambda: packages)
    skill = register_user_skill(SkillRegisterRequest(
        name="unique-file-skill-xyz",
        description="文件权威",
        content="只应出现在 SKILL.md",
    ))
    rows = [row for row in cached_skill_records() if row["id"] == skill.id]
    assert rows
    assert "只应出现在 SKILL.md" in rows[0]["content"]
    markdown = read_package_markdown(skill.resource_path)
    assert "只应出现在 SKILL.md" in markdown


def test_project_skill_refs_allow_without_binding(tmp_path) -> None:
    skill_dir = tmp_path / ".agents" / "skills" / "local-writer"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: local-writer\ndescription: Write locally.\n---\n\nDo it.\n",
        encoding="utf-8",
    )
    refs = project_skill_refs(tmp_path)
    assert "local-writer" in refs


def test_system_skill_name_cannot_be_registered() -> None:
    with pytest.raises(Exception) as exc:
        register_user_skill(SkillRegisterRequest(
            name="word-documents",
            description="dup",
            content="nope",
        ))
    assert getattr(exc.value, "status_code", None) == 409
