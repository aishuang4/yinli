"""标准 Skill 包注册与导出测试。"""

from __future__ import annotations

import zipfile
from io import BytesIO

import pytest

from src.storage import database


def test_imported_skill_requires_standard_frontmatter_fields():
    """AgentScope loader 所需的 name/description 缺失时应在安装阶段失败。"""

    import src.server.routes.skills as skills_routes

    with pytest.raises(Exception) as exc_info:
        skills_routes._skill_from_markdown(
            "missing-description/SKILL.md",
            "---\nname: missing-description\n---\n\nInstructions.\n",
        )

    assert getattr(exc_info.value, "status_code", None) == 422


def test_skill_package_diagnostics_are_non_blocking():
    """目录名和标准名称不一致时返回 warning，但仍能创建 Skill。"""

    import src.server.routes.skills as skills_routes

    skill = skills_routes._skill_from_markdown(
        "Different Folder/SKILL.md",
        "---\nname: Workbook Inspector\ndescription: Inspect workbooks.\n---\n\nInstructions.\n",
        resources=["SKILL.md", "scripts/helper.bin"],
    )

    assert len(skill.warnings) == 3
    assert any("目录名" in warning for warning in skill.warnings)
    assert any("小写字母" in warning for warning in skill.warnings)
    assert any("helper.bin" in warning for warning in skill.warnings)


def test_skill_package_warns_about_platform_specific_scripts():
    """导入的 Shell 与 PowerShell 脚本应明确提示可执行平台。"""

    import src.server.routes.skills as skills_routes

    skill = skills_routes._skill_from_markdown(
        "portable-skill/SKILL.md",
        "---\nname: portable-skill\ndescription: Run platform helpers.\n---\n",
        resources=["SKILL.md", "scripts/run.sh", "scripts/setup.ps1"],
    )

    assert any(".sh/.bash" in warning and "Windows" in warning for warning in skill.warnings)
    assert any(".ps1" in warning and "PowerShell 7+" in warning for warning in skill.warnings)


def test_skill_zip_rejects_symbolic_links():
    """Skill zip 不允许通过符号链接把资源解析到包目录之外。"""

    import src.server.routes.skills as skills_routes

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "safe/SKILL.md",
            "---\nname: safe\ndescription: Safe skill.\n---\n\nInstructions.\n",
        )
        link = zipfile.ZipInfo("safe/scripts/link.py")
        link.create_system = 3
        link.external_attr = (0o120777 << 16)
        archive.writestr(link, "../../outside.py")

    with pytest.raises(Exception) as exc_info:
        skills_routes._read_skill_md_from_zip(buffer.getvalue())

    assert getattr(exc_info.value, "status_code", None) == 422


def test_import_skill_package_ignores_build_artifacts_and_macos_metadata(tmp_path, monkeypatch):
    """导入时只保留标准包资源，不能复制构建产物的重复副本。"""

    import src.server.routes.skills as skills_routes

    monkeypatch.setattr(skills_routes, "SKILL_PACKAGES_DIR", str(tmp_path / "skill_packages"))
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "word-documents/SKILL.md",
            "---\nname: word-documents\ndescription: Create Word documents.\n---\n",
        )
        archive.writestr("word-documents/scripts/create_docx.py", "print('ok')\n")
        archive.writestr("word-documents/.DS_Store", "metadata")
        archive.writestr("artifacts/skills/word-documents/SKILL.md", "duplicate")
        archive.writestr("artifacts/skills/word-documents/scripts/create_docx.py", "duplicate")

    skill_path, _markdown, archive, members = skills_routes._read_skill_md_from_zip(buffer.getvalue())
    try:
        resource_path, resources = skills_routes._extract_skill_package(
            archive, members, skill_path, "word-documents",
        )
    finally:
        archive.close()

    assert resources == ["SKILL.md", "scripts/create_docx.py"]
    assert (tmp_path / "skill_packages" / "word-documents" / "scripts" / "create_docx.py").exists()
    assert not (tmp_path / "skill_packages" / "word-documents" / "artifacts").exists()
    assert resource_path == str(tmp_path / "skill_packages" / "word-documents")


def test_existing_skill_package_cleans_duplicate_artifacts(tmp_path, monkeypatch):
    """启动回填历史 Skill 时清理旧版本留下的 artifacts 副本。"""

    import src.server.routes.skills as skills_routes

    package_dir = tmp_path / "skill_packages" / "word-documents"
    (package_dir / "scripts").mkdir(parents=True)
    (package_dir / "SKILL.md").write_text("---\nname: word-documents\ndescription: Word.\n---\n", encoding="utf-8")
    (package_dir / "scripts" / "create_docx.py").write_text("print('ok')\n", encoding="utf-8")
    (package_dir / "artifacts" / "skills" / "word-documents").mkdir(parents=True)
    (package_dir / "artifacts" / "skills" / "word-documents" / "SKILL.md").write_text("duplicate", encoding="utf-8")
    monkeypatch.setattr(skills_routes, "SKILL_PACKAGES_DIR", str(tmp_path / "skill_packages"))
    skill = skills_routes.Skill.model_validate({
        "id": "custom-word-documents",
        "name": "word-documents",
        "description": "Word.",
        "category": "导入",
        "resourcePath": str(package_dir),
        "resources": ["SKILL.md", "artifacts/skills/word-documents/SKILL.md"],
        "status": "installed",
        "updatedAt": "2026-07-20T00:00:00+00:00",
    })

    repaired = skills_routes._ensure_skill_package(skill)

    assert repaired.resources == ["SKILL.md", "scripts/create_docx.py"]
    assert not (package_dir / "artifacts").exists()


def test_register_user_skill_writes_standard_skill_package(tmp_path, monkeypatch):
    """手动注册 Skill 时必须落标准 SKILL.md 包目录。"""

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "agent_workstation.db")
    monkeypatch.setattr(database, "_initialized", False)
    import src.server.routes.skills as skills_routes

    original_skills = dict(skills_routes._skills)
    monkeypatch.setattr(skills_routes, "SKILL_PACKAGES_DIR", str(tmp_path / "skill_packages"))
    try:
        skill = skills_routes.register_user_skill(
            skills_routes.SkillRegisterRequest(
                name="API 文档检查",
                description="检查 API 文档是否完整。",
                category="文档",
                trigger="用户要求检查 API 文档时使用。",
                content="1. 检查参数说明。\n2. 检查错误码。\n",
                tags=["api", "docs"],
            )
        )

        skill_md = tmp_path / "skill_packages" / "API 文档检查" / "SKILL.md"
        assert skill.resource_path == str(tmp_path / "skill_packages" / "API 文档检查")
        assert skill.resources == ["SKILL.md"]
        assert skill_md.exists()
        assert "name: API 文档检查" in skill_md.read_text(encoding="utf-8")
        assert "检查参数说明" in skill.content
    finally:
        skills_routes._skills.clear()
        skills_routes._skills.update(original_skills)


def test_export_skill_archive_contains_standard_skill_md(tmp_path, monkeypatch):
    """导出 Skill 时 zip 内必须包含标准 SKILL.md。"""

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "agent_workstation.db")
    monkeypatch.setattr(database, "_initialized", False)
    import src.server.routes.skills as skills_routes

    original_skills = dict(skills_routes._skills)
    monkeypatch.setattr(skills_routes, "SKILL_PACKAGES_DIR", str(tmp_path / "skill_packages"))
    try:
        skill = skills_routes.register_user_skill(
            skills_routes.SkillRegisterRequest(
                name="变更摘要",
                description="总结代码变更。",
                content="按新增、修复、风险输出摘要。",
                tags=["summary"],
            )
        )

        raw = skills_routes._skill_export_archive(skill)
        with zipfile.ZipFile(BytesIO(raw)) as archive:
            skill_path = "变更摘要/SKILL.md"
            assert skill_path in archive.namelist()
            content = archive.read(skill_path).decode("utf-8")
            assert "name: 变更摘要" in content
            assert "按新增、修复、风险输出摘要" in content
        assert skills_routes._skill_export_filename(skill) == "变更摘要.zip"
    finally:
        skills_routes._skills.clear()
        skills_routes._skills.update(original_skills)


def test_load_user_skills_backfills_missing_standard_package(tmp_path, monkeypatch):
    """历史 registry 中缺包目录的 Skill 加载时会自动补齐。"""

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "agent_workstation.db")
    monkeypatch.setattr(database, "_initialized", False)
    import src.server.routes.skills as skills_routes

    original_skills = dict(skills_routes._skills)
    monkeypatch.setattr(skills_routes, "SKILL_PACKAGES_DIR", str(tmp_path / "skill_packages"))
    try:
        database.replace_skills({
            "custom-old-skill": {
                "id": "custom-old-skill",
                "name": "旧 Skill",
                "description": "历史注册的 Skill。",
                "category": "自定义",
                "trigger": "需要旧 Skill 时使用。",
                "content": "执行历史流程。",
                "tags": ["legacy"],
                "resourcePath": "",
                "resources": [],
                "status": "installed",
                "enabled": True,
                "installedAt": "2026-07-06T00:00:00+00:00",
                "updatedAt": "2026-07-06T00:00:00+00:00",
            }
        })
        skills_routes._skills.clear()
        skills_routes._skills.update(skills_routes._builtin_skills)

        skills_routes._load_user_skills()

        skill = skills_routes._skills["custom-old-skill"]
        skill_md = tmp_path / "skill_packages" / "旧 Skill" / "SKILL.md"
        assert skill.resource_path == str(tmp_path / "skill_packages" / "旧 Skill")
        assert skill.resources == ["SKILL.md"]
        assert skill_md.exists()
        assert "name: 旧 Skill" in skill_md.read_text(encoding="utf-8")
        assert database.list_skills()[0]["resourcePath"] == skill.resource_path
    finally:
        skills_routes._skills.clear()
        skills_routes._skills.update(original_skills)


def test_register_user_skill_rejects_duplicate_name(tmp_path, monkeypatch):
    """相同 Skill 名称不能重复注册。"""

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "agent_workstation.db")
    monkeypatch.setattr(database, "_initialized", False)
    import src.server.routes.skills as skills_routes

    original_skills = dict(skills_routes._skills)
    monkeypatch.setattr(skills_routes, "SKILL_PACKAGES_DIR", str(tmp_path / "skill_packages"))
    try:
        payload = skills_routes.SkillRegisterRequest(
            name="重复 Skill",
            description="第一次注册。",
            content="执行流程。",
        )
        skills_routes.register_user_skill(payload)

        with pytest.raises(Exception) as exc_info:
            skills_routes.register_user_skill(
                skills_routes.SkillRegisterRequest(
                    name="重复-Skill",
                    description="第二次注册。",
                    content="执行另一个流程。",
                )
            )

        assert getattr(exc_info.value, "status_code", None) == 409
        assert len([
            skill
            for skill in skills_routes._skills.values()
            if "重复" in skill.name
        ]) == 1
    finally:
        skills_routes._skills.clear()
        skills_routes._skills.update(original_skills)
