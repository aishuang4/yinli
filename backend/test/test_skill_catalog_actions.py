"""磁盘 Skill 的安装、卸载、删除，以及详情随 SKILL.md 更新。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from src.server.routes import skills as routes
from src.skills.registry import SkillInstallRequest


def _write_skill(package: Path, *, name: str, description: str, body: str) -> None:
    package.mkdir(parents=True, exist_ok=True)
    (package / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n",
        encoding="utf-8",
    )


def _by_name(skills: list, name: str):
    found = [skill for skill in skills if skill.name == name]
    assert len(found) == 1
    return found[0]


def test_disk_skills_can_be_installed_uninstalled_and_deleted(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "project"
    package = project / ".agents" / "skills" / "bugfix-writer"
    _write_skill(package, name="bugfix-writer", description="旧说明", body="旧正文")
    monkeypatch.setattr("src.skills.registry.runtime_user_home_root", lambda: None)
    monkeypatch.setattr(
        "src.server.routes.skills.get_project",
        lambda project_id: {"slug": project_id, "primaryPath": str(project)},
    )

    listed = asyncio.run(routes.list_skills(project_slug="project-1"))
    skill = _by_name(listed, "bugfix-writer")
    assert skill.status == "installed"
    assert skill.description == "旧说明"
    assert "旧正文" in skill.content

    _write_skill(package, name="bugfix-writer", description="新说明", body="新正文")
    refreshed = asyncio.run(routes.list_skills(project_slug="project-1"))
    skill = _by_name(refreshed, "bugfix-writer")
    assert skill.description == "新说明"
    assert "新正文" in skill.content

    uninstalled = asyncio.run(routes.uninstall_skill(skill.id, "project-1"))
    assert uninstalled.status == "available"
    assert uninstalled.enabled is False

    installed = asyncio.run(
        routes.install_skill(uninstalled.id, SkillInstallRequest(enabled=True), "project-1"),
    )
    assert installed.status == "installed"
    assert installed.enabled is True

    asyncio.run(routes.delete_skill(installed.id, "project-1"))
    assert not package.exists()
    remaining = asyncio.run(routes.list_skills(project_slug="project-1"))
    assert all(item.name != "bugfix-writer" for item in remaining)


def test_user_scope_skill_installs_instead_of_404(tmp_path: Path, monkeypatch) -> None:
    from src.config.runtime_paths import tenant_root

    project = tmp_path / "empty-project"
    project.mkdir()
    package = tenant_root() / ".agents" / "skills" / "bugfix-notes"
    _write_skill(package, name="bugfix-notes", description="用户技能", body="步骤")
    monkeypatch.setattr("src.skills.registry.runtime_user_home_root", lambda: None)
    monkeypatch.setattr(
        "src.server.routes.skills.get_project",
        lambda project_id: {"slug": project_id, "primaryPath": str(project)},
    )

    listed = asyncio.run(routes.list_skills(project_slug="project-1"))
    skill = _by_name(listed, "bugfix-notes")
    assert skill.status == "available"
    installed = asyncio.run(
        routes.install_skill(skill.id, SkillInstallRequest(), "project-1"),
    )
    assert installed.status == "installed"
    assert installed.enabled is True


def test_registered_skill_detail_rereads_package() -> None:
    from src.config.runtime_paths import skill_packages_root

    package = skill_packages_root() / "bugfix-registered"
    _write_skill(package, name="bugfix-registered", description="第一版", body="第一版正文")
    listed = asyncio.run(routes.list_skills())
    current = _by_name(listed, "bugfix-registered")
    assert current.description == "第一版"
    assert "第一版正文" in current.content
    _write_skill(package, name="bugfix-registered", description="第二版", body="第二版正文")
    again = asyncio.run(routes.list_skills())
    current = _by_name(again, "bugfix-registered")
    assert current.description == "第二版"
    assert "第二版正文" in current.content
